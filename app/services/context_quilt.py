"""
Context Quilt integration for GhostPour.

Handles two flows:
  1. Recall: Before sending a query to the LLM, fetch relevant context from CQ
  2. Capture: After the LLM responds, send query+response to CQ for learning

Both flows are controlled by the `context_quilt: true` flag in ChatRequest.

Auth: If CQ_CLIENT_SECRET is set, uses JWT bearer tokens (auto-refreshing).
Otherwise falls back to X-App-ID header (legacy, for backwards compat).
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.services.cq_subject import subject_for

logger = logging.getLogger(__name__)

# Ring size for the recall debug dumps (see _debug_dump_recall).
_RECALL_DUMP_KEEP = 5


def _debug_dump_recall(body: dict, result: dict) -> None:
    """Persist the exact outbound /v1/recall body and CQ's exact response
    to a small ring of files beside the DB (same volume convention as the
    version-gate overlay). Lane verification against CQ needs both ends
    byte-exact: the outbound metadata proves what GP forwarded
    (memory_signals passthrough), and the returned block diffs line for
    line against CQ's reference render (recall output is byte-stable
    within a UTC day). The only other copy of the block lives inside
    usage_log raw_request, already wrapped for the LLM. Never allowed to
    break recall."""
    try:
        from app import database
        base = (
            Path(database._db_path).parent
            if getattr(database, "_db_path", None)
            else Path("data")
        )
        dump_dir = base / "cq_recall_debug"
        dump_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        (dump_dir / f"recall-{ts}.json").write_text(
            json.dumps({"sent": body, "received": result}, ensure_ascii=False, indent=2)
        )
        for old in sorted(dump_dir.glob("recall-*.json"))[:-_RECALL_DUMP_KEEP]:
            old.unlink()
    except Exception as e:  # noqa: BLE001
        logger.warning("cq_recall_debug_dump_failed", extra={"error": str(e)})

# Shared HTTP client (created on first use)
_client: httpx.AsyncClient | None = None

# JWT token cache, keyed by CQ app_id — per-app identities each cache their own.
_tokens: dict[str, tuple[str, float]] = {}  # cq_app_id -> (token, expires_at)

# How long to stop asking after CQ rejects a credential. Long enough that a
# wrong secret cannot hammer their pbkdf2 token endpoint at one hash per
# request, short enough that fixing the secret takes effect without a
# restart. A rejected credential is permanent until a human intervenes, so
# there is nothing to gain from asking sooner.
AUTH_FAILURE_COOLDOWN_SECONDS = 60.0
_auth_failures: dict[str, float] = {}  # cq_app_id -> retry_after (epoch)

# One rejection is not enough to stop asking, and this is a joint problem
# rather than a hypothetical (CQ, 2026-08-07).
#
# CQ's token endpoint has an outer arm that turns ANY failure into a
# credential error, so a database outage on their side presents as
# "Incorrect client_id or client_secret". They deliberately deferred fixing
# that status code because a behaviour change on a live auth path during
# GP's cutover week is the wrong trade. Reasonable in isolation.
#
# Then we shipped the cooldown, and the two compose badly: a brief blip on
# their side now reads to us as a permanently wrong credential and we stop
# talking to them for a minute. Before the cooldown we would have retried
# and recovered instantly. Neither side could see that from its own code.
#
# So: a credential does not spontaneously become wrong. If we HELD a working
# token for this identity, a rejection now is far more likely to be their
# outage than our secret changing under us, and it earns a retry. Two in a
# row with no success between them is a real rejection.
_auth_strikes: dict[str, int] = {}  # cq_app_id -> consecutive rejections


def _reset_auth_failure(cq_app: str) -> None:
    """Forget a rejection. Called on a successful mint so a fixed secret
    recovers immediately rather than serving out the rest of the cooldown,
    and so a later blip starts from a clean slate rather than inheriting a
    strike from an unrelated failure minutes earlier."""
    _auth_failures.pop(cq_app, None)
    _auth_strikes.pop(cq_app, None)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.cq_base_url,
            timeout=httpx.Timeout(5.0),  # General timeout; recall uses its own
        )
    return _client


def _cq_identity(app_id: str | None) -> tuple[str, str]:
    """The (cq_app_id, cq_client_secret) GP authenticates to CQ with for a given
    GP app. Per-app identities (apps.yml apps.<id>.cq) let a second CQ app (Tech
    Rehearsal, its own CQ app_id) ride GP under its own identity so CQ loads the
    right schema. Falls back to the default (ShoulderSurf / ghostpour) identity."""
    settings = get_settings()
    default = (settings.cq_app_id, settings.cq_client_secret)
    if not app_id:
        return default
    try:
        from app.routers.config import load_apps
        entry = (load_apps().get("apps", {}).get(app_id.strip().lower()) or {}).get("cq") or {}
    except Exception:
        return default
    cq_app = entry.get("app_id")
    if not cq_app:
        return default
    secret = getattr(settings, entry.get("secret_setting") or "", "") or ""
    return (cq_app, secret)


async def _get_auth_headers(app_id: str | None = None) -> dict[str, str]:
    """Auth headers for CQ requests, for the CQ identity of `app_id`. When the
    caller doesn't pass one, fall back to the requesting app from the
    contextvar — before that fallback existed, every call site that omitted
    the argument silently authenticated as the default identity, which made
    per-app identity look wired while never actually being used on the wire.
    JWT bearer when a secret is configured, else X-App-ID."""
    if app_id is None:
        from app.request_context import current_app_id
        app_id = current_app_id.get()
    cq_app, cq_secret = _cq_identity(app_id)

    if not cq_secret:
        # Legacy / not-yet-provisioned: forward the app tag (CQ may accept it).
        return {"X-App-ID": cq_app}

    # JWT auth, cached per CQ app: refresh if expired or within the 30s buffer.
    cached = _tokens.get(cq_app)
    if cached and time.time() < cached[1] - 30:
        return {"Authorization": f"Bearer {cached[0]}"}

    # A REJECTED credential is remembered too, and this half matters more
    # than the success cache (CQ, 2026-08-07).
    #
    # Success was cached and failure was not, so with a wrong secret every
    # proxied request minted a fresh token. CQ's /v1/auth/token verifies with
    # pbkdf2_sha256, deliberately CPU costly, and has no rate limiting. So a
    # bad credential did not merely fail, it generated sustained load on the
    # one endpoint designed to be slow, at one hash per request.
    #
    # CQ framed this as our 401-to-502 translation inviting client retries.
    # It is worse than that: no retrying client is required. Ordinary traffic
    # was the load.
    #
    # A wrong credential is PERMANENT. Retrying it sooner than a human can
    # fix it has no upside, so we stop asking for a cooldown and fail fast
    # locally. Transient failures (timeout, 5xx, unreachable) are NOT
    # cooled down: those genuinely may succeed on the next call.
    failed_until = _auth_failures.get(cq_app)
    if failed_until and time.time() < failed_until:
        return {"X-App-ID": cq_app}

    try:
        client = _get_client()
        resp = await client.post(
            "/v1/auth/token",
            data={"username": cq_app, "password": cq_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_data = resp.json()
        token = token_data["access_token"]
        _tokens[cq_app] = (token, time.time() + token_data.get("expires_in", 3600))
        _reset_auth_failure(cq_app)
        logger.info("cq_token_refreshed", extra={"cq_app": cq_app, "expires_in": token_data.get("expires_in")})
        return {"Authorization": f"Bearer {token}"}

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (400, 401, 403):
            # The credential itself is wrong: unknown app id, or a secret
            # that did not travel intact. Nothing changes until a human
            # changes it, so stop asking for a while.
            strikes = _auth_strikes.get(cq_app, 0) + 1
            _auth_strikes[cq_app] = strikes
            if strikes >= 2:
                _auth_failures[cq_app] = time.time() + AUTH_FAILURE_COOLDOWN_SECONDS
                logger.error(
                    "cq_token_rejected",
                    extra={"cq_app": cq_app, "status": status, "strikes": strikes,
                           "cooldown_s": AUTH_FAILURE_COOLDOWN_SECONDS},
                )
            else:
                # Logged at WARNING, not ERROR: a single rejection against a
                # backend that reports outages as credential errors is not
                # yet evidence of a bad credential.
                logger.warning(
                    "cq_token_rejected_once",
                    extra={"cq_app": cq_app, "status": status, "strikes": strikes},
                )
        else:
            logger.warning("cq_token_error",
                           extra={"error": str(e), "cq_app": cq_app, "status": status})
        return {"X-App-ID": cq_app}
    except Exception as e:
        # Timeout, connection refused, malformed body. Possibly transient, so
        # no cooldown: cooling these down would extend a blip into a minute of
        # degraded auth for no reason.
        logger.warning("cq_token_error", extra={"error": str(e), "cq_app": cq_app})
        return {"X-App-ID": cq_app}


async def recall(
    user_id: str,
    text: str,
    metadata: dict | None = None,
    subscription_tier: str | None = None,
    app_id: str | None = None,
) -> dict:
    """
    Fetch relevant context from Context Quilt's graph memory.

    Returns:
        {
            "context": "formatted text block",
            "matched_entities": ["entity names"],
            "patch_count": int
        }
    Returns empty result on timeout, error, or if CQ is not configured.
    """
    settings = get_settings()
    if not settings.cq_base_url:
        return {"context": "", "matched_entities": [], "patch_count": 0}

    timeout_sec = settings.cq_recall_timeout_ms / 1000.0

    body: dict[str, Any] = {
        # The per-app subject, not the raw GP user id. SS resolves to the
        # bare id so nothing it has written moves; a second app gets its
        # own space. See app/services/cq_subject.py for why this exists.
        "user_id": subject_for(app_id, user_id),
        "text": text,
    }
    merged_metadata = dict(metadata) if metadata else {}
    if subscription_tier:
        merged_metadata["subscription_tier"] = subscription_tier
    if merged_metadata:
        body["metadata"] = merged_metadata

    try:
        client = _get_client()
        auth_headers = await _get_auth_headers(app_id)
        resp = await client.post(
            "/v1/recall",
            json=body,
            headers=auth_headers,
            timeout=httpx.Timeout(timeout_sec),
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "cq_recall_ok",
            extra={
                "matched": len(result.get("matched_entities", [])),
                "patch_count": result.get("patch_count", 0),
                # Contract v1 lane check: what GP actually forwarded, so a
                # device-side flip is verifiable from this one log line.
                "memory_signals": merged_metadata.get("memory_signals", "absent"),
                # Free-lane volume tripwire, the recall twin of the
                # 500-free-captures-per-30d capture watch. Free users
                # generated ZERO recall calls before the people lane, so
                # scoped volume is a brand-new load shape; this field
                # makes it one log query at the edge, and CQ can count
                # the same lane from the metadata it receives
                # (recall_scope and subscription_tier ride the body).
                # Absent key means the full render, hence the default.
                "recall_scope": merged_metadata.get("recall_scope", "full"),
            },
        )
        _debug_dump_recall(body, result)
        return result

    # Degrades are ERROR, not WARNING: the turn still answers, but WITHOUT
    # its memory block, and the user can't tell (2026-07-18: a 200ms
    # timeout silently ate the contract-test turn and was only caught
    # forensically). Context fields make the lost turn identifiable
    # without a dump.
    except httpx.TimeoutException:
        logger.error(
            "cq_recall_degraded reason=timeout — turn proceeds WITHOUT memory block",
            extra={
                "timeout_ms": settings.cq_recall_timeout_ms,
                "project": merged_metadata.get("project"),
                "memory_signals": merged_metadata.get("memory_signals", "absent"),
                "recall_scope": merged_metadata.get("recall_scope", "full"),
            },
        )
        return {"context": "", "matched_entities": [], "patch_count": 0}
    except Exception as e:
        logger.error(
            "cq_recall_degraded reason=error — turn proceeds WITHOUT memory block",
            extra={
                "error": str(e),
                "project": merged_metadata.get("project"),
                "memory_signals": merged_metadata.get("memory_signals", "absent"),
                "recall_scope": merged_metadata.get("recall_scope", "full"),
            },
        )
        return {"context": "", "matched_entities": [], "patch_count": 0}


# Client-supplied metadata keys forwarded verbatim on capture. One
# auditable list, so adding a key is one edit here rather than three
# across two files, and so what we forward can be reviewed at a glance.
# Server-derived identity and entitlement fields are NOT here: they are
# explicit parameters, because a client must not be able to set them by
# putting them in a request body.
CAPTURE_METADATA_ALLOWLIST = frozenset({
    "user_identified",
    "user_label",
    "identification_source",
    "language",
    "call_type",
    "prompt_mode",
    "scenario",
    "scenario_kind",
    "transcript_source",
})


async def capture(
    user_id: str,
    interaction_type: str,
    content: str,
    app_id: str | None = None,
    response: str | None = None,
    origin_id: str | None = None,
    origin_type: str | None = None,
    meeting_id: str | None = None,  # DEPRECATED — use origin_id + origin_type
    project: str | None = None,
    project_id: str | None = None,
    call_type: str | None = None,
    prompt_mode: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    user_identified: bool | None = None,
    user_label: str | None = None,
    identification_source: str | None = None,
    subscription_tier: str | None = None,
    language: str | None = None,
    context_block: str | None = None,
    passthrough: dict[str, Any] | None = None,
):
    """
    Send query+response to Context Quilt for learning. Fire-and-forget (async).

    This runs in the background after the LLM response is returned to the user.
    Never blocks the response.

    Origin scoping: CQ v1 replaced meeting_id with (origin_id, origin_type).
    Callers should pass origin_id + origin_type directly. The meeting_id arg
    is retained as a deprecated alias — when supplied, it's forwarded as
    origin_id with origin_type="meeting".
    """
    settings = get_settings()
    if not settings.cq_base_url:
        return

    body: dict[str, Any] = {
        # Per-app subject, same as recall. This is the one that MATTERS:
        # capture is what creates memory, so an unnamespaced write here is
        # what would actually commingle two apps for a shared user.
        "user_id": subject_for(app_id, user_id),
        "interaction_type": interaction_type,
        "content": content,
    }
    if response:
        body["response"] = response
    if context_block:
        # Correction lane (contract item 9): the recall block that was on
        # the user's screen — CQ builds its contradicted-patch candidate
        # set from these lines first, scoped matching second.
        body["context_block"] = context_block

    # Normalize origin: prefer explicit origin_id/origin_type; fall back to
    # translating the deprecated meeting_id alias.
    if origin_id is None and meeting_id is not None:
        origin_id = meeting_id
        origin_type = origin_type or "meeting"

    # Client-supplied metadata rides an auditable allowlist, the same
    # extension point recall uses. Before this, every key was enumerated by
    # hand in TWO places (get_meta in the route, then a named parameter and
    # a conditional here), so adding one cost three edits across two files
    # and missing any of them produced a key that was accepted by pydantic,
    # present in the request object, and read by nothing. `language` is the
    # precedent: it had to be threaded through all three.
    #
    # Server-derived fields stay explicit parameters below, because they
    # come from the user record rather than the client and must not be
    # spoofable by putting them in a request body.
    metadata: dict[str, Any] = {
        k: v for k, v in (passthrough or {}).items()
        if k in CAPTURE_METADATA_ALLOWLIST and v is not None
    }
    if origin_id:
        metadata["origin_id"] = origin_id
    if origin_type:
        metadata["origin_type"] = origin_type
    if project:
        metadata["project"] = project
    if project_id:
        metadata["project_id"] = project_id
    if call_type:
        metadata["call_type"] = call_type
    if prompt_mode:
        metadata["prompt_mode"] = prompt_mode
    if display_name:
        metadata["display_name"] = display_name
    if email:
        metadata["email"] = email
    if user_identified is not None:
        metadata["user_identified"] = user_identified
    if user_label:
        metadata["user_label"] = user_label
    if identification_source:
        metadata["identification_source"] = identification_source
    if subscription_tier:
        metadata["subscription_tier"] = subscription_tier
    # BCP-47 tag (full tags fine, e.g. "es-US"). CQ writes extracted memory
    # text in this language; when absent it infers from the speaker's words,
    # which guesses wrong in mixed-language meetings.
    if language:
        metadata["language"] = language
    if metadata:
        body["metadata"] = metadata

    try:
        client = _get_client()
        auth_headers = await _get_auth_headers(app_id)
        resp = await client.post(
            "/v1/memory",
            json=body,
            headers=auth_headers,
        )
        resp.raise_for_status()
        logger.info("cq_capture_ok", extra={"type": interaction_type})
    except Exception as e:
        logger.warning("cq_capture_error", extra={"error": str(e)})


async def notify_tier_change(
    user_id: str,
    old_tier: str,
    new_tier: str,
    event_type: str,
    occurred_at: str | None = None,
    offer_id: str | None = None,
    app_id: str | None = None,
    environment: str | None = None,
):
    """Notify Context Quilt of a subscription tier transition.

    Fire-and-forget. CQ uses these events to drive retention/soft-delete
    policy without GP having to encode the policy on its side.

    event_type values: "upgrade", "downgrade", "cancellation", "refund",
    "expire", "trial_start", "trial_to_paid", "account_deleted"
    (new_tier "deleted"; CQ's cue to purge everything it holds for the
    user — wired 2026-07-25 for App Review 5.1.1(v)). Idempotent on
    (user_id, occurred_at) on the CQ side.

    `app_id` selects the CQ identity the signal rides under (apps with
    their own CQ app each hold a separate quilt). Account deletion is
    scoped per app, so the purge cue must reach only the deleting app's
    quilt; None keeps the default identity, which is what every
    subscription-driven caller wants since tier is account-wide.
    """
    # Purchase ops alert (2026-07-27): every tier transition already
    # funnels through here from all seven call sites, so this is the one
    # chokepoint. Must run BEFORE the cq_base_url early-return — the
    # operator wants the email even when CQ is unreachable/unconfigured.
    # No-ops for non-paid event types; never raises.
    # offer_id (ASC offer reference name, when the purchase redeemed an
    # offer code) feeds ONLY the ops email — it is not part of the CQ
    # wire shape below.
    from app.services.subscription_alerts import notify_purchase
    await notify_purchase(user_id, old_tier, new_tier, event_type,
                          offer_id=offer_id, environment=environment)

    # Subscriber welcome letter: queued ~1h out on the first paid event,
    # never for offer-code gifts. Best-effort like everything else here.
    from app.services.welcome_email import enqueue as welcome_enqueue
    await welcome_enqueue(user_id, new_tier, event_type, offer_id=offer_id)

    settings = get_settings()
    if not settings.cq_base_url:
        return

    from datetime import datetime, timezone
    body = {
        "old_tier": old_tier,
        "new_tier": new_tier,
        "event_type": event_type,
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
    }

    try:
        client = _get_client()
        auth_headers = await _get_auth_headers(app_id)
        resp = await client.post(
            f"/v1/users/{user_id}/tier-change",
            json=body,
            headers=auth_headers,
        )
        resp.raise_for_status()
        logger.info(
            "cq_tier_change_ok",
            extra={"user_id": user_id, "old": old_tier, "new": new_tier, "event": event_type},
        )
    except Exception as e:
        logger.warning(
            "cq_tier_change_error",
            extra={"user_id": user_id, "event": event_type, "error": str(e)},
        )


# --- Rundown routing (Context Flow Contract v1, item 3) ---
#
# Recall is a ranked injection block by design; inventory-style questions
# ("give me everything across all meetings") need the whole project
# dossier. GP detects those deterministically on the question portion,
# fails open to normal recall, and injects the meeting-grouped quilt
# instead of the recall block.

_RUNDOWN_HINTS = (
    # en
    "everything you have", "everything you know", "everything you remember",
    "everything from memory", "everything in memory", "all memories",
    "all the memories", "all your memories", "complete rundown",
    "full rundown", "complete summary across", "complete history",
    "brain dump", "as much information as you can",
    "all commitments and blockers across",
    # es
    "todo lo que sabes", "todo lo que tienes", "todas las memorias",
    "resumen completo de todo",
    # ja
    "すべての記憶", "全ての記憶", "覚えていることをすべて",
)

DOSSIER_LIMIT = 150  # CQ suggested 100-150; tune after the three-way test


def is_rundown_ask(question: str) -> bool:
    """Deterministic, conservative: misses fall open to normal recall
    (the contract's design), so the list optimizes precision."""
    q = (question or "").lower()
    return any(h in q for h in _RUNDOWN_HINTS)


async def quilt_dossier(user_id: str, project_id: str | None,
                        app_id: str | None = None,
                        limit: int | None = DOSSIER_LIMIT,
                        max_age_days: int | None = None) -> dict | None:
    """GET /v1/quilt/{user_id}?project_id&group_by=origin&limit — the
    complete scoped memory, meeting-grouped, newest first. None on any
    failure (caller falls back to recall).

    `limit=None` OMITS the parameter, which is how CQ returns everything:
    their cap is caller-supplied and absent by default. That is the right
    call for an artifact whose job is COUNTING, where a truncated input
    produces a confidently wrong number in a cell. Measured 2026-08-17:
    Scott's own quilt reports `total_available` 2136 against the 500 we
    were asking for, so the counting artifact was seeing under a quarter
    of the material and had no way to say so.
    """
    settings = get_settings()
    if not settings.cq_base_url:
        return None
    try:
        client = _get_client()
        resp = await client.get(
            f"/v1/quilt/{user_id}",
            # project_id is a FILTER, not the boundary (CQ, 2026-08-15):
            # omitting it returns the user-scoped sync and group_by=origin
            # still yields the meetings array. That is what lets a chat
            # turn with no project still compute recurrence, at the cost
            # of pulling every project at once, so the cap matters MORE
            # there rather than less.
            params={**({"project_id": project_id} if project_id else {}),
                    "group_by": "origin",
                    # Omitted entirely when None: CQ defaults to no cap,
                    # and sending one is what makes them truncate.
                    **({"limit": limit} if limit is not None else {}),
                    # Plus recall window (CQ #297, second commit): same
                    # predicate as /v1/recall, applied before the count so
                    # total_available is the windowed population. Absent =
                    # untouched; 0 is a 422 on their side, never a sentinel.
                    **({"max_age_days": max_age_days}
                       if isinstance(max_age_days, int) and not isinstance(max_age_days, bool)
                       and max_age_days >= 1 else {})},
            headers=await _get_auth_headers(app_id),
            timeout=httpx.Timeout(settings.cq_dossier_timeout_ms / 1000.0),
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "cq_dossier_ok project=%s meetings=%d flat_facts=%d actions=%d",
            project_id, len(data.get("meetings") or []),
            len(data.get("facts") or []), len(data.get("action_items") or []),
        )
        return data
    except Exception as e:
        logger.warning("cq_dossier_failed project=%s: %s %s — falling back to recall",
                       project_id, type(e).__name__, e)
        return None


def _format_patch(p: dict) -> str:
    bits = [f"[{p.get('patch_type') or p.get('category') or 'fact'}] {p.get('fact', '')}".rstrip()]
    if p.get("owner"):
        bits.append(f"(owner: {p['owner']})")
    if p.get("deadline_date") or p.get("deadline"):
        bits.append(f"(deadline: {p.get('deadline_date') or p.get('deadline')})")
    return " ".join(bits)


def _is_shelved(patch: dict) -> bool:
    """Did the user set this one aside?

    CQ stamps `shelved_at` and deliberately leaves the patch ACTIVE: it
    keeps flowing through /v1/quilt and their recall still finds it, so
    the assistant can still answer "did Vijay ever owe me the hardware
    POC?". That is the right call for a question ABOUT the past and the
    wrong one for this block, which is a list of what is still owed. A
    user who taps "Let it go" and then hears the assistant chase the
    item anyway does not experience a subtle distinction between a
    to-do list and a memory; they experience the button not working.

    Verified on the wire 2026-08-19 rather than read off a design note:
    `shelved_at` is a TOP-LEVEL key on every patch row CQ returns (86 of
    86 on a real account, null on all of them today, because prod has
    zero shelved items so far). It is deliberately not read out of
    `metadata` -- that nesting is real for our capture REQUEST shape and
    is not how their response rows are built.

    This filter covers this block only. Their recall path is a separate
    query that intentionally still surfaces shelved items, so it is not
    fixed here and was never ours to fix.
    """
    return bool(patch.get("shelved_at"))


def format_dossier(data: dict, limit: int | None = DOSSIER_LIMIT) -> str:
    """The injection block. Meeting-grouped, newest first (CQ's ordering);
    origin-less patches (user-scoped) follow in flat sections. server_time
    is never rendered — the block must stay byte-stable within CQ's
    stability window (contract item 6) for prompt caching."""
    lines: list[str] = []
    seen: set = set()
    total = 0
    shelved = 0
    meetings = data.get("meetings") or []
    for i, m in enumerate(meetings, 1):
        rendered = []
        for p in (m.get("patches") or []):
            if p.get("patch_id") in seen:
                continue
            seen.add(p.get("patch_id"))
            if _is_shelved(p):
                shelved += 1
                continue
            rendered.append(p)
        # A meeting with nothing left to show gets no heading. Previously
        # this could only happen through dedup; shelving makes it likely,
        # and a bare "## Meeting 3 of 5" with no patches under it is a
        # confusing thing to put in a prompt.
        if not rendered:
            continue
        stamp = (rendered[0].get("created_at") or "")[:10]
        lines.append(f"## Meeting {i} of {len(meetings)}"
                     + (f" ({stamp})" if stamp else ""))
        for p in rendered:
            lines.append(_format_patch(p))
            total += 1
        lines.append("")
    flat = []
    for key in ("action_items", "facts"):
        for p in (data.get(key) or []):
            if p.get("patch_id") in seen:
                continue
            if _is_shelved(p):
                seen.add(p.get("patch_id"))
                shelved += 1
                continue
            flat.append(p)
    if flat:
        lines.append("## Not tied to a specific meeting")
        for p in flat:
            seen.add(p.get("patch_id"))
            lines.append(_format_patch(p))
            total += 1
        lines.append("")
    # NO completeness claim. The block holds what it holds, and the count
    # is the count of what is rendered. "complete stored memory" was the
    # word that turned an omission into a lie: once shelved patches are
    # filtered out the block is NOT complete stored memory, so the claim
    # was false and the footnote existed only to repair it (CQ, 19.6).
    # Describing the contents needs no footnote.
    header = (f"[PROJECT MEMORY DOSSIER: "
              f"{total} patches across {len(meetings)} meetings]")
    # The shelved count goes to the log, NOT into the block. The counting
    # artifact is code and can be told out of band; the model is the one
    # consumer that must not be told, because telling it hands it
    # something to say. A line reading "1 shelved patch omitted, do not
    # chase it" is a fact plus an instruction about that fact, and the
    # failure is the assistant announcing "there is one thing you set
    # aside" to the user whose whole request was that it stop coming up.
    # The disclosure meant to prevent a leak becomes the leak.
    if shelved:
        logger.info("cq_dossier_shelved_omitted count=%d rendered=%d",
                    shelved, total)
    # Prefer CQ's own disclosure over inferring one. They now return
    # `truncated` and `total_available`, and the total is counted BEFORE
    # the cap, so it is the real denominator rather than a guess. The old
    # `total >= limit` test could only ever say "possibly", and said it
    # wrongly whenever the count landed on the cap by coincidence.
    if data.get("truncated"):
        _avail = data.get("total_available")
        header += ("\n(dossier truncated: showing " + str(total) + " of "
                   + (str(_avail) if _avail is not None else "more")
                   + " stored patches, so counts here are a FLOOR)")
    elif limit is not None and total >= limit:
        header += f"\n(dossier capped at the {limit} most recent patches)"
    return header + "\n\n" + "\n".join(lines).strip()


# --- Correction lane (Context Flow Contract item 9) ---
#
# A user who spots a wrong memory in a chat answer corrects it in place
# ("set the record straight, Robin owns that"). GP detects it
# deterministically and captures interaction_type="correction" carrying
# the user's words (NEVER the model's response) plus scope and the
# recall block that was in context. CQ extracts the corrected fact as a
# declared patch, matches the contradicted patch (in-context candidates
# first), archives it, connects with role "replaces". Unmatched
# corrections land as regular declared patches — never dropped — so
# this list optimizes PRECISION: a false positive creates a junk patch.

_CORRECTION_HINTS = (
    # en
    "set the record straight", "correct the record", "correct that memory",
    "correction:", "for the record,", "update the record",
    "update your memory", "fix the memory", "fix that memory",
    "that memory is wrong", "the record should say",
    "your memory is wrong about", "remember it as",
    # es
    "corrige el registro", "para que conste,", "corrige esa memoria",
    # ja
    "記録を訂正", "記憶を修正",
)


def is_correction_ask(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in _CORRECTION_HINTS)


# --- Completion lane (Context Flow Contract item 10) ---
#
# "That blocker is done" said in chat actually closes it — same pipe as
# tap-to-complete, flowing the completed array through delta sync. The
# stakes are HIGHER than corrections: a false-positive correction makes
# a junk patch; a false-positive completion closes a real commitment.
# The hint list is therefore even tighter — explicit done/resolved
# statements only, never questions or futures.

_COMPLETION_HINTS = (
    # en — declarative completion statements
    "mark that as done", "mark it as done", "mark that complete",
    "mark it complete", "that blocker is done", "that blocker is resolved",
    "that task is done", "that's done now", "that is done now",
    "we finished that", "consider it done", "that commitment is complete",
    "close that out", "you can close that",
    # es
    "marca eso como hecho", "eso ya está resuelto", "ciérralo",
    # ja
    "完了にして", "それは完了した",
)


def is_completion_ask(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in _COMPLETION_HINTS)
