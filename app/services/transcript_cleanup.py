"""Server-side captions/STT transcript cleanup.

Runs an LLM rewrite pass over a raw transcript before report or analysis
generation, to dedupe scroll duplicates, strip OCR garbage tokens,
canonicalize speaker names, and fold mis-attributed speaker-label leaks.

Wire contract with iOS:
- Request carries `transcript_source` field on report POST and /v1/chat body
  (values: "ocr_captions", "speech_to_text", "mixed")
- Response carries optional `cleaned_transcript` string field
- iOS falls back to raw transcript silently when the field is absent

Routing strategy (primary + fallback):
- Primary: DeepSeek V3.2-exp via OpenRouter. Matches Haiku 4.5 quality
  on the eval (~0.81 ROUGE) at roughly 1/8 the cost. Has shown latency
  and quality variance across runs, so we cap it at PRIMARY_TIMEOUT_SECS
  and fall back if it times out or returns empty.
- Fallback: Anthropic Haiku 4.5 direct. Slower but very consistent
  (6-16s, stable quality). Caught the variance gap when the primary
  misbehaves.

We skip cleanup (silently, omit cleaned_transcript) when:
- The server flag is off
- transcript_source is absent, "mixed", or unknown
- No cleanup prompt is configured for the source/locale
- Input is empty or above MAX_INPUT_CHARS
- BOTH the primary and the fallback fail (provider error, timeout, empty)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from app.models.chat import ChatRequest, ChatResponse
from app.services.provider_router import ProviderRouter

# Callback invoked once per successful cleanup attempt so the caller can meter
# the sub-call (cost + usage_log row). Receives the ChatRequest we built, the
# provider response, and the call's elapsed time in ms. See clean_transcript.
SubcallMeter = Callable[[ChatRequest, ChatResponse, int], Awaitable[None]]

logger = logging.getLogger("ghostpour.transcript_cleanup")

# Sources we currently know how to clean. "speech_to_text" will be added
# once we have a tuned STT prompt; "mixed" is intentionally excluded
# because picking the wrong cleanup prompt is worse than no cleanup.
_CLEANABLE_SOURCES = {"ocr_captions"}

# Primary cleanup model. Picked via eval 2026-05-31 against a real OCR
# meeting + ground truth: DeepSeek V3.2-exp matched Haiku 4.5's ROUGE
# (0.812 vs 0.815) at ~13% of the cost (~$0.028 vs $0.22 per 1hr meeting
# projection). Routed through OpenRouter because the model is not on
# DeepSeek's direct API; OR is wired as a chat provider in
# config/providers.yml for this and any future routed models.
_PRIMARY_PROVIDER = "openrouter"
_PRIMARY_MODEL = "deepseek/deepseek-v3.2-exp"

# Hard ceiling on the primary call. The old 30s cap was tuned for short
# transcripts and silently defeated the primary on full meetings: a 2026-06-22
# A/B on a real ~21K-char meeting showed DeepSeek finishing in 63-161s (avg
# ~112s), so 30s ALWAYS timed out → every real meeting fell back to Haiku,
# paying ~10x the cost and ~3pt lower accuracy for nothing. Raised to 120s so
# DeepSeek completes on normal runs (the better + 10x-cheaper result), with
# Haiku fallback reserved for genuine 120s+ outliers. Tradeoff: cleanup runs
# before report generation, so this can add up to ~120s to the report wait.
_PRIMARY_TIMEOUT_SECS = 120.0

# Fallback model. Anthropic Haiku 4.5 direct (already wired in
# config/providers.yml under the `anthropic` provider). Tighter latency
# distribution (6-16s observed) and lower quality variance than the
# primary. Used when the primary times out or returns empty content.
_FALLBACK_PROVIDER = "anthropic"
_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

# Maximum raw transcript length we'll attempt to clean. Beyond this we
# skip and let downstream processing run on the raw text. Set
# conservatively to keep token budgets bounded; raise once we see real
# production usage.
MAX_INPUT_CHARS = 200_000


def should_clean(transcript_source: str | None, feature_enabled: bool) -> bool:
    """Decide whether to attempt cleanup for this request."""
    if not feature_enabled:
        return False
    if not transcript_source:
        return False
    if transcript_source not in _CLEANABLE_SOURCES:
        return False
    return True


def get_cleanup_prompt(
    remote_configs: dict,
    source: str,
    locale: str = "en",
) -> Optional[str]:
    """Load the cleanup prompt for the given source. Locale-specific lookup
    falls back to English when the localized variant doesn't carry the field.
    Returns None when no prompt is configured.
    """
    if locale and locale != "en":
        cfg = remote_configs.get(f"protected-prompts.{locale}") or {}
        prompt = (cfg.get("transcriptCleanup") or {}).get(source)
        if prompt:
            return prompt
    cfg = remote_configs.get("protected-prompts") or {}
    return (cfg.get("transcriptCleanup") or {}).get(source)


async def _attempt(
    provider_router: ProviderRouter,
    *,
    provider: str,
    model: str,
    system_prompt: str,
    raw_transcript: str,
    meeting_id: str | None,
    timeout: float | None,
) -> "tuple[ChatRequest, ChatResponse, int] | None":
    """Single-model attempt with optional wall-clock timeout. Returns
    (request, response, elapsed_ms) on success, None on any failure mode the
    caller should treat as "try the next route". The request is returned so the
    caller can meter the sub-call. Logs a one-line attribution per attempt.
    """
    request = ChatRequest(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_content=raw_transcript,
        max_tokens=8000,
        call_type="captions_cleanup",
        prompt_mode="CaptionsTranscriptCleanup",
        meeting_id=meeting_id,
    )
    start = time.monotonic()
    try:
        if timeout is not None:
            response = await asyncio.wait_for(
                provider_router.route(request), timeout=timeout,
            )
        else:
            response = await provider_router.route(request)
    except asyncio.TimeoutError:
        logger.warning(
            "Transcript cleanup timed out meeting=%s provider=%s model=%s timeout=%.1fs",
            meeting_id, provider, model, timeout,
        )
        return None
    except Exception as e:
        logger.warning(
            "Transcript cleanup error meeting=%s provider=%s model=%s err=%s",
            meeting_id, provider, model, e,
        )
        return None

    elapsed_ms = int((time.monotonic() - start) * 1000)
    cleaned = (getattr(response, "text", "") or "").strip()
    if not cleaned:
        logger.warning(
            "Transcript cleanup returned empty meeting=%s provider=%s model=%s",
            meeting_id, provider, model,
        )
        return None
    return request, response, elapsed_ms


async def clean_transcript(
    provider_router: ProviderRouter,
    raw_transcript: str,
    remote_configs: dict,
    transcript_source: str,
    *,
    locale: str = "en",
    meeting_id: str | None = None,
    on_subcall: "SubcallMeter | None" = None,
) -> Optional[str]:
    """Run the LLM cleanup pass. Returns the cleaned transcript on success,
    None on any failure (caller falls back to raw and omits the field).

    Tries the primary model first. If the primary times out (the bad-run
    band) or returns empty content, falls back to the secondary model.
    Both failing returns None.

    on_subcall, when provided, is awaited once for the winning attempt with
    (request, response, elapsed_ms) so the caller can meter the cleanup as its
    own usage_log row + cost. Metering failures are swallowed (logged) so they
    never drop an otherwise-good cleaned transcript.
    """
    if not raw_transcript or not raw_transcript.strip():
        return None
    if len(raw_transcript) > MAX_INPUT_CHARS:
        logger.warning(
            "Transcript cleanup skipped: %d chars exceeds %d limit (meeting=%s)",
            len(raw_transcript), MAX_INPUT_CHARS, meeting_id,
        )
        return None

    system_prompt = get_cleanup_prompt(remote_configs, transcript_source, locale)
    if not system_prompt:
        logger.warning(
            "Transcript cleanup skipped: no prompt for source=%s locale=%s",
            transcript_source, locale,
        )
        return None

    # Primary attempt with hard timeout
    result = await _attempt(
        provider_router,
        provider=_PRIMARY_PROVIDER,
        model=_PRIMARY_MODEL,
        system_prompt=system_prompt,
        raw_transcript=raw_transcript,
        meeting_id=meeting_id,
        timeout=_PRIMARY_TIMEOUT_SECS,
    )
    if result:
        request, response, elapsed_ms = result
        cleaned = response.text.strip()
        ratio = len(cleaned) / len(raw_transcript)
        logger.info(
            "Transcript cleanup ok (primary) meeting=%s source=%s model=%s in_chars=%d out_chars=%d ratio=%.2f",
            meeting_id, transcript_source, _PRIMARY_MODEL,
            len(raw_transcript), len(cleaned), ratio,
        )
        await _meter(on_subcall, request, response, elapsed_ms, meeting_id)
        return cleaned

    # Fallback attempt — no timeout (Haiku's variance is much tighter)
    logger.info(
        "Transcript cleanup falling back meeting=%s from=%s to=%s",
        meeting_id, _PRIMARY_MODEL, _FALLBACK_MODEL,
    )
    result = await _attempt(
        provider_router,
        provider=_FALLBACK_PROVIDER,
        model=_FALLBACK_MODEL,
        system_prompt=system_prompt,
        raw_transcript=raw_transcript,
        meeting_id=meeting_id,
        timeout=None,
    )
    if result:
        request, response, elapsed_ms = result
        cleaned = response.text.strip()
        ratio = len(cleaned) / len(raw_transcript)
        logger.info(
            "Transcript cleanup ok (fallback) meeting=%s source=%s model=%s in_chars=%d out_chars=%d ratio=%.2f",
            meeting_id, transcript_source, _FALLBACK_MODEL,
            len(raw_transcript), len(cleaned), ratio,
        )
        await _meter(on_subcall, request, response, elapsed_ms, meeting_id)
        return cleaned

    logger.warning(
        "Transcript cleanup both routes failed meeting=%s source=%s",
        meeting_id, transcript_source,
    )
    return None


async def _meter(
    on_subcall: "SubcallMeter | None",
    request: ChatRequest,
    response: ChatResponse,
    elapsed_ms: int,
    meeting_id: str | None,
) -> None:
    """Run the caller's metering callback, swallowing any failure so a logging
    or pricing hiccup never costs us the cleaned transcript."""
    if on_subcall is None:
        return
    try:
        await on_subcall(request, response, elapsed_ms)
    except Exception as e:
        logger.warning(
            "Transcript cleanup metering failed meeting=%s err=%s", meeting_id, e,
        )
