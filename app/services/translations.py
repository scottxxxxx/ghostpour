"""Meeting translations engine (docs/wire-contracts/meeting-translations.md).

One engine, two client uses: the post-meeting toggle and the
share-import offer. The client sends segment GROUPS of {id, text} only;
labels and timestamps never ride this wire, so reassembly by id is the
client's and structure cannot be corrupted here by construction.

ENGINE_VERSION is the prompt recipe version (dossier:
docs/prompt-dossiers/translations.md). Bump it whenever the prompt
changes; the cache key carries it, so stored renditions never silently
change under a client.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

# One source of truth for the card budget: if meeting_title raises or
# lowers what it is willing to generate, what we translate follows it.
# Imported as the MODULE, not `from ... import MAX_TITLE_CHARS`, so the
# link is live at call time. A from-import is a snapshot taken at import
# and a duplicated literal is indistinguishable from it at runtime,
# because `60 is 60` is True for any small int CPython has interned.
from app.services import meeting_title

logger = logging.getLogger("ghostpour.translations")

ENGINE_VERSION = 1

# Server-controlled, never on the wire (client sends no model choice).
TRANSLATION_PROVIDER = "anthropic"
TRANSLATION_MODEL = "claude-haiku-4-5-20251001"

ARTIFACTS = ("transcript", "summary", "report", "title")

# Transcript translation is FAITHFUL: no dash rule, the user's own words
# keep their punctuation (same carve-out as transcriptCleanup).
_PROMPT_BASE = (
    "You translate meeting content segments. Input is a JSON array of "
    "{id, text} objects in the source language. Return ONLY a JSON array "
    "of {id, text} objects: every id present exactly once, in the same "
    "order, each text translated to the target language. Translate "
    "faithfully: keep the register, fillers, and punctuation style of "
    "the speaker; never summarize, never omit, never add. Personal "
    "names, company names, and product names are never translated."
)
_PROMPT_PROSE_EXTRA = (
    " Never use em dashes or en dashes anywhere in your output, even "
    "when the source contains them; use a comma, colon, or parentheses "
    "instead."
)


# A meeting TITLE is a name, not prose, and it is the one artifact with a
# hard display budget. It was missing from ARTIFACTS until 2026-08-31, so a
# translated meeting kept its English headline forever while transcript,
# summary and report all swapped. SS's reading of that symptom is why this
# is worth its own prompt rather than being folded into `summary`: a
# control that works invisibly is indistinguishable from one that does not
# work, and three of four visible fields swapping reads as BROKEN where
# zero would read as "not implemented".
_PROMPT_TITLE_EXTRA = (
    " Every text here is the TITLE OF ONE MEETING: a name, not a sentence. "
    "Return a name. No trailing period, no article you had to invent, no "
    "explanatory clause. Never make a title more generic than the one you "
    "were given: a title that no longer says WHICH meeting this was has "
    "failed, even if it is shorter and reads well. Keep each title at or "
    "under 60 characters where the language allows it; when a literal "
    "rendering would run longer, choose the shorter faithful wording "
    "rather than padding or truncating mid-word."
)


def system_prompt(artifact: str) -> str:
    if artifact == "transcript":
        return _PROMPT_BASE
    if artifact == "title":
        return _PROMPT_BASE + _PROMPT_PROSE_EXTRA + _PROMPT_TITLE_EXTRA
    return _PROMPT_BASE + _PROMPT_PROSE_EXTRA


def over_budget_titles(segments: list[dict]) -> list[str]:
    """Ids whose translated title exceeds the client's card budget.

    DELIBERATELY NON-BLOCKING, and the asymmetry with meeting_title.py is
    the point. That module rejects a GENERATED title server-side because
    absent beats generic: the client treats a served title as
    authoritative and skips its own fallback, so a bad title we send is a
    bad title that renders. Neither half of that reasoning survives here.
    We are not inventing a name, we are carrying an already-accepted one
    across a language, and the state we would fall back to is the ENGLISH
    title on a Spanish card, which is the exact defect this artifact
    exists to remove. So an over-budget title still ships and truncates in
    the client. This makes the rate MEASURABLE instead of invisible, which
    is all it claims to do.

    Counts only ever reach the log: a meeting title is user content.
    """
    budget = meeting_title.MAX_TITLE_CHARS
    return [s["id"] for s in segments
            if isinstance(s.get("text"), str) and len(s["text"]) > budget]


def normalize_language(tag: Any) -> str | None:
    """BCP-47-ish sanity: 2-3 letter primary subtag, optional subtags."""
    if not isinstance(tag, str):
        return None
    tag = tag.strip()
    if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", tag):
        return tag
    return None


def cache_key(segments: list[dict], source: str, target: str) -> str:
    """sha256(canonical segment-group JSON) + source + target +
    engine_version — the contract's idempotency key. Canonicalisation
    matches the config-manifest convention (sorted keys, compact
    separators, ensure_ascii=False)."""
    canon = json.dumps(segments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"{h}:{source}:{target}:v{ENGINE_VERSION}"


async def cache_get(db: aiosqlite.Connection, key: str) -> list[dict] | None:
    row = await (await db.execute(
        "SELECT response_json FROM translations_cache WHERE cache_key = ?", (key,)
    )).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["response_json"])
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_put(
    db: aiosqlite.Connection, key: str, artifact: str,
    source: str, target: str, segments: list[dict],
) -> None:
    await db.execute(
        """INSERT OR REPLACE INTO translations_cache
           (cache_key, artifact, source_language, target_language,
            engine_version, response_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key, artifact, source, target, ENGINE_VERSION,
         json.dumps(segments, ensure_ascii=False),
         datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


async def purge_transcript_translations(db: aiosqlite.Connection, max_age_days: int = 30) -> int:
    """Retention: cached TRANSCRIPT translations inherit transcript
    retention (30-day purge, retroactive) — the cache never outlives its
    source. Summary and report renditions follow report retention and
    are not purged here."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    cur = await db.execute(
        "DELETE FROM translations_cache WHERE artifact = 'transcript' AND created_at < ?",
        (cutoff,),
    )
    await db.commit()
    if cur.rowcount:
        logger.info("translations_purge transcript rows=%d", cur.rowcount)
    return cur.rowcount


def parse_model_output(text: str, expected_ids: list[str]) -> list[dict] | None:
    """Server-side parse of the model's JSON. Fences and prose wrapping
    are stripped HERE — the wire to the client carries clean JSON only,
    which is why the contract forbids client-side fence stripping."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(out, list):
        return None
    cleaned = []
    for o in out:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) \
                or not isinstance(o.get("text"), str):
            return None
        cleaned.append({"id": o["id"], "text": o["text"]})
    if [o["id"] for o in cleaned] != expected_ids:
        return None
    return cleaned


# ---------------------------------------------------------------------------
# The engine as a service call, used by POST /v1/translations and by the
# share page's transcript picker. Raises HTTPException on budget block or
# provider failure so both callers surface the same shapes.

class TranslationBlocked(Exception):
    """The paying user's monthly allocation is exhausted (the only gate)."""


class TranslationFailed(Exception):
    """Provider error or an output that never round-tripped its ids."""


async def translate_group(app_state, db: aiosqlite.Connection, user, segments: list[dict],
                          source: str, target: str, artifact: str, app_id: str | None,
                          request_id: str | None = None) -> tuple[list[dict], bool]:
    """Translate one segment group for `user` (who pays). Returns
    (segments, cached). Cache first; budget pre-gate; one retry on a bad
    round-trip; metering as call_type=translation on success or error."""
    import json as _json
    import time as _time
    from app.models.chat import ChatRequest

    key = cache_key(segments, source, target)
    hit = await cache_get(db, key)
    if hit is not None:
        return hit, True

    tier = app_state.tier_config.tiers.get(user.effective_tier)
    if tier is None:
        raise TranslationBlocked("unknown_tier")
    effective_limit = tier.monthly_cost_limit_usd
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        effective_limit = tier.trial_cost_limit_usd
    if effective_limit != -1:
        row = await (await db.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?", (user.id,))).fetchone()
        monthly_used = float(row["monthly_used_usd"] or 0) if row else 0.0
        if monthly_used >= effective_limit:
            raise TranslationBlocked("allocation_exhausted")

    expected_ids = [s["id"] for s in segments]
    chat_request = ChatRequest(
        provider=TRANSLATION_PROVIDER, model=TRANSLATION_MODEL,
        system_prompt=system_prompt(artifact),
        user_content=f"Target language: {target}. Source language: {source}.\n"
                     + _json.dumps(segments, ensure_ascii=False),
        max_tokens=8192, temperature=0.2,
        metadata={"call_type": "translation", "request_id": request_id,
                  "translation_artifact": artifact, "translation_segments": len(segments),
                  "translation_source": source, "translation_target": target},
    )
    start = _time.monotonic()
    out = None
    response = None
    for attempt in (1, 2):
        try:
            response = await app_state.provider_router.route(chat_request)
        except Exception as e:  # noqa: BLE001
            logger.error("translation LLM call failed: %s", e)
            raise TranslationFailed("provider_error")
        out = parse_model_output(response.text or "", expected_ids)
        if out is not None:
            if artifact == "title":
                over = over_budget_titles(out)
                if over:
                    logger.info(
                        "translation_title_over_budget target=%s over=%d of=%d budget=%d",
                        target, len(over), len(out), meeting_title.MAX_TITLE_CHARS)
            break
        logger.warning("translation id round-trip failed attempt=%d", attempt)
    elapsed_ms = int((_time.monotonic() - start) * 1000)

    pricing = app_state.pricing
    usage_tracker = app_state.usage_tracker
    request_cost = 0.0
    if pricing.is_loaded and response is not None:
        cost = pricing.calculate_cost(
            provider=TRANSLATION_PROVIDER, model=TRANSLATION_MODEL, usage=response.usage,
            input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        response.cost = cost
        request_cost = cost.get("total_cost", 0.0)
    await usage_tracker.record_cost(db, user.id, request_cost, tier, user=user)
    await usage_tracker.log_usage(
        db, user.id, chat_request, response, elapsed_ms,
        status="success" if out is not None else "error",
        error_msg=None if out is not None else "id_round_trip_failed",
        app_id=app_id or "unknown")
    if out is None:
        raise TranslationFailed("translation_shape_error")
    await cache_put(db, key, artifact, source, target, out)
    return out, False
