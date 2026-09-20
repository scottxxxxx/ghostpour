"""Warm the interviewer lane's prompt cache before she says anything.

Prod, 2026-09-20: the lane's first turn of a session waited 9.7s for the
stream to open, and every later turn about 1.3s. The system prompt is about
24k tokens and the first turn after the cache lapses reprocesses all of it.
The one hour cache (providers/anthropic.py) takes that away from almost
everybody. This takes it away from the first applicant of the hour: the
client fires a warm up when the Talk screen opens, and the cache is written
while she is still reading the screen.

THE PREFIX IS COPIED FROM A REAL TURN, NEVER REBUILT. The chat route
rewrites the system prompt in a dozen conditional places after assembly
(memory line, language line, locale, caps). A warm up that renders "the same
prompt" by its own path warms an entry nothing will ever read, costs six
cents, and reports success. So `remember()` records the final provider
facing prefix of every real turn, and a warm up replays that. It is also
written to `warm_prefixes`, because Anthropic's cache outlives our
restarts and a deploy must not forget what to warm. With nothing recorded
yet, the warm up says so and spends nothing.

And the drop is AUDIBLE: when a real turn's prefix differs from the one
last warmed for that lane, `remember()` logs `n400_warmup_prefix_mismatch`.
That is a changed prompt (expected, once per cut) or the two paths drifting
(a defect), and either way the warm up stopped paying for itself.

THREE WAYS IT SPENDS NOTHING
  already_warm    GP sees every user's turns; a real turn or a warm up on
                  this prefix inside the cache's life means it is warm.
                  The client sees one device and cannot know this.
  skipped_recent  one warm up per user per THROTTLE_SECONDS whatever the
                  client does. It is an authenticated call that can cost six
                  cents, so a client bug that loops it must not be a bill.
  no_prefix_yet   nothing to copy.

It is not a turn: no guards, no dedupe, no chat_turns row, no dossier, no
turn cap, not rate limited, never falls back to another provider (a warm up
that falls back warms nothing). It writes a usage_log row under its own
call_type so the spend is visible, and deducts nothing from her allocation
until Scott rules on that.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

from app.models.chat import ChatRequest

logger = logging.getLogger("ghostpour.n400_warmup")

LANE_CALL_TYPE = "n400_interviewer_turn"
WARMUP_CALL_TYPE = "n400_interviewer_warmup"
THROTTLE_SECONDS = 240
# How long after a call on a prefix we take the cache to be alive: the
# entry's life less a margin, because the life runs from the START of the
# request that touched it and ours is measured from its end.
WARM_FOR_SECONDS = {"1h": 55 * 60, None: 4 * 60}

_PREFIXES: dict[str, dict] = {}            # lane_key -> prefix
_TOUCHED: dict[str, float] = {}            # prefix_sha -> monotonic time of last call
_LAST_WARMED_SHA: dict[str, str] = {}      # lane_key -> sha the last warm up sent
_LAST_WARMUP_BY_USER: dict[str, float] = {}


def lane_key(app_id: str | None, locale: str | None) -> str:
    return f"{app_id or '-'}|{LANE_CALL_TYPE}|{(locale or 'en').lower()}"


def _sha(provider: str, model: str, system_prompt: str, thinking: str | None) -> str:
    h = hashlib.sha256()
    for part in (provider, model, thinking or "", system_prompt):
        h.update(part.encode("utf-8")); h.update(b"\x00")
    return h.hexdigest()[:32]


def _cache_life_seconds(request: ChatRequest) -> int:
    from app.services.providers.anthropic import _system_cache_control
    return WARM_FOR_SECONDS.get(_system_cache_control(request).get("ttl"), WARM_FOR_SECONDS[None])


async def remember(db, app_id: str | None, body: ChatRequest) -> None:
    """Called with the FINAL request of a real interviewer turn, just before
    it goes to the provider. Never raises: a real turn must not be able to
    fail because of the warm up's bookkeeping."""
    try:
        if body.get_meta("call_type") != LANE_CALL_TYPE or body.provider != "anthropic":
            return
        key = lane_key(app_id, body.get_meta("locale") or body.locale)
        sha = _sha(body.provider, body.model, body.system_prompt or "", body.thinking)
        _TOUCHED[sha] = time.monotonic()
        warmed = _LAST_WARMED_SHA.get(key)
        if warmed is not None and warmed != sha:
            logger.warning("n400_warmup_prefix_mismatch lane=%s warmed_sha=%s turn_sha=%s "
                           "(a new prompt cut, or the warm up and the turn have drifted)",
                           key, warmed, sha)
            _LAST_WARMED_SHA.pop(key, None)
        known = _PREFIXES.get(key)
        if known is not None and known["prefix_sha"] == sha:
            return
        prefix = {"provider": body.provider, "model": body.model,
                  "system_prompt": body.system_prompt or "", "thinking": body.thinking,
                  "prefix_sha": sha}
        _PREFIXES[key] = prefix
        await db.execute(
            """INSERT OR REPLACE INTO warm_prefixes
               (lane_key, provider, model, system_prompt, thinking, prefix_sha, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, prefix["provider"], prefix["model"], prefix["system_prompt"],
             prefix["thinking"], sha, datetime.now(timezone.utc).isoformat()))
        await db.commit()
        logger.info("n400_warmup_prefix_recorded lane=%s sha=%s system_chars=%d",
                    key, sha, len(prefix["system_prompt"]))
    except Exception as e:  # noqa: BLE001
        logger.warning("n400 warm up bookkeeping failed, the turn is unaffected: %s: %s",
                       type(e).__name__, e)


async def _prefix_for(db, key: str) -> dict | None:
    if key in _PREFIXES:
        return _PREFIXES[key]
    row = await (await db.execute(
        "SELECT provider, model, system_prompt, thinking, prefix_sha FROM warm_prefixes "
        "WHERE lane_key = ?", (key,))).fetchone()
    if row is None:
        return None
    _PREFIXES[key] = dict(zip(("provider", "model", "system_prompt", "thinking", "prefix_sha"), row))
    return _PREFIXES[key]


async def warm(*, db, provider_router, usage_tracker, pricing, user_id: str,
               app_id: str | None, locale: str | None) -> dict:
    """The response body. Always a dict, never raises: a failed warm up must
    never surface to her or delay her first turn."""
    key = lane_key(app_id, locale)
    try:
        now = time.monotonic()
        last = _LAST_WARMUP_BY_USER.get(user_id)
        if last is not None and now - last < THROTTLE_SECONDS:
            return {"warmed": True, "outcome": "skipped_recent",
                    "retry_after_seconds": int(THROTTLE_SECONDS - (now - last))}
        prefix = await _prefix_for(db, key)
        if prefix is None:
            return {"warmed": False, "reason": "no_prefix_yet"}
        request = ChatRequest(
            provider=prefix["provider"], model=prefix["model"],
            system_prompt=prefix["system_prompt"], user_content="[warm up]",
            thinking=prefix["thinking"], stream=False, prewarm=True,
            # The LANE's call_type, because the adapter picks the cache life
            # from it and a different life is a different request.
            metadata={"call_type": LANE_CALL_TYPE, "locale": locale})
        touched = _TOUCHED.get(prefix["prefix_sha"])
        if touched is not None and now - touched < _cache_life_seconds(request):
            return {"warmed": True, "outcome": "already_warm",
                    "seconds_since_last_call": int(now - touched)}
        _LAST_WARMUP_BY_USER[user_id] = now
        start = time.monotonic()
        # provider_router.route, NOT route_with_fallback: a warm up that
        # falls back to another provider warms nothing and still bills.
        response = await provider_router.route(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _TOUCHED[prefix["prefix_sha"]] = time.monotonic()
        _LAST_WARMED_SHA[key] = prefix["prefix_sha"]
        usage = response.usage or {}
        written = int(usage.get("cache_creation_input_tokens") or 0)
        read = int(usage.get("cache_read_input_tokens") or 0)
        if getattr(pricing, "is_loaded", False):
            response.cost = pricing.calculate_cost(
                provider=request.provider, model=request.model, usage=response.usage,
                input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        # Logged under its OWN call_type so the spend is attributable, and
        # NOT deducted from her allocation (Scott has not ruled on that).
        logged = request.model_copy(update={"metadata": {"call_type": WARMUP_CALL_TYPE,
                                                         "locale": locale}})
        await usage_tracker.log_usage(db, user_id, logged, response, elapsed_ms, app_id=app_id)
        if not written and not read:
            # The prefix is under the cacheable minimum or the marker was
            # lost. Either way nothing was warmed and the money was spent.
            logger.warning("n400_warmup_cached_nothing lane=%s sha=%s", key, prefix["prefix_sha"])
        logger.info("n400_warmup lane=%s outcome=%s write=%d read=%d ms=%d", key,
                    "written" if written else "already_warm", written, read, elapsed_ms)
        return {"warmed": bool(written or read),
                "outcome": "written" if written else ("already_warm" if read else "cached_nothing"),
                "cache_write_tokens": written, "cache_read_tokens": read, "ms": elapsed_ms}
    except Exception as e:  # noqa: BLE001
        logger.warning("n400 warm up failed: %s: %s", type(e).__name__, e)
        return {"warmed": False, "reason": "failed"}
