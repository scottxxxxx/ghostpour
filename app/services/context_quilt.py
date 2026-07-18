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
    """Auth headers for CQ requests, for the CQ identity of `app_id` (the default
    identity when None). JWT bearer when a secret is configured, else X-App-ID."""
    cq_app, cq_secret = _cq_identity(app_id)

    if not cq_secret:
        # Legacy / not-yet-provisioned: forward the app tag (CQ may accept it).
        return {"X-App-ID": cq_app}

    # JWT auth, cached per CQ app: refresh if expired or within the 30s buffer.
    cached = _tokens.get(cq_app)
    if cached and time.time() < cached[1] - 30:
        return {"Authorization": f"Bearer {cached[0]}"}

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
        logger.info("cq_token_refreshed", extra={"cq_app": cq_app, "expires_in": token_data.get("expires_in")})
        return {"Authorization": f"Bearer {token}"}

    except Exception as e:
        logger.warning("cq_token_error", extra={"error": str(e), "cq_app": cq_app})
        return {"X-App-ID": cq_app}


async def recall(
    user_id: str,
    text: str,
    metadata: dict | None = None,
    subscription_tier: str | None = None,
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
        "user_id": user_id,
        "text": text,
    }
    merged_metadata = dict(metadata) if metadata else {}
    if subscription_tier:
        merged_metadata["subscription_tier"] = subscription_tier
    if merged_metadata:
        body["metadata"] = merged_metadata

    try:
        client = _get_client()
        auth_headers = await _get_auth_headers()
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
            },
        )
        return {"context": "", "matched_entities": [], "patch_count": 0}


async def capture(
    user_id: str,
    interaction_type: str,
    content: str,
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
        "user_id": user_id,
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

    # Build metadata from available fields
    metadata: dict[str, Any] = {}
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
        auth_headers = await _get_auth_headers()
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
):
    """Notify Context Quilt of a subscription tier transition.

    Fire-and-forget. CQ uses these events to drive retention/soft-delete
    policy without GP having to encode the policy on its side.

    event_type values: "upgrade", "downgrade", "cancellation", "refund",
    "expire", "trial_start", "trial_to_paid". Idempotent on
    (user_id, occurred_at) on the CQ side.
    """
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
        auth_headers = await _get_auth_headers()
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


async def quilt_dossier(user_id: str, project_id: str,
                        limit: int = DOSSIER_LIMIT) -> dict | None:
    """GET /v1/quilt/{user_id}?project_id&group_by=origin&limit — the
    complete scoped memory, meeting-grouped, newest first. None on any
    failure (caller falls back to recall)."""
    settings = get_settings()
    if not settings.cq_base_url:
        return None
    try:
        client = _get_client()
        resp = await client.get(
            f"/v1/quilt/{user_id}",
            params={"project_id": project_id, "group_by": "origin",
                    "limit": limit},
            headers=await _get_auth_headers(),
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


def format_dossier(data: dict, limit: int = DOSSIER_LIMIT) -> str:
    """The injection block. Meeting-grouped, newest first (CQ's ordering);
    origin-less patches (user-scoped) follow in flat sections. server_time
    is never rendered — the block must stay byte-stable within CQ's
    stability window (contract item 6) for prompt caching."""
    lines: list[str] = []
    seen: set = set()
    total = 0
    meetings = data.get("meetings") or []
    for i, m in enumerate(meetings, 1):
        patches = m.get("patches") or []
        if not patches:
            continue
        stamp = (patches[0].get("created_at") or "")[:10]
        lines.append(f"## Meeting {i} of {len(meetings)}"
                     + (f" — {stamp}" if stamp else ""))
        for p in patches:
            if p.get("patch_id") in seen:
                continue
            seen.add(p.get("patch_id"))
            lines.append(_format_patch(p))
            total += 1
        lines.append("")
    flat = [p for key in ("action_items", "facts")
            for p in (data.get(key) or []) if p.get("patch_id") not in seen]
    if flat:
        lines.append("## Not tied to a specific meeting")
        for p in flat:
            seen.add(p.get("patch_id"))
            lines.append(_format_patch(p))
            total += 1
        lines.append("")
    header = (f"[PROJECT MEMORY DOSSIER — complete stored memory: "
              f"{total} patches across {len(meetings)} meetings]")
    if total >= limit:
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
