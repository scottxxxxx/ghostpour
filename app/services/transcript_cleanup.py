"""Server-side captions/STT transcript cleanup.

Runs an LLM rewrite pass over a raw transcript before report or analysis
generation, to dedupe scroll duplicates, strip OCR garbage tokens,
canonicalize speaker names, and fold mis-attributed speaker-label leaks.

Wire contract with iOS:
- Request carries `transcript_source` field on report POST and /v1/chat body
  (values: "ocr_captions", "speech_to_text", "mixed")
- Response carries optional `cleaned_transcript` string field
- iOS falls back to raw transcript silently when the field is absent

We skip cleanup (silently, omit cleaned_transcript) when:
- The server flag is off
- transcript_source is absent, "mixed", or unknown
- No cleanup prompt is configured for the source/locale
- Input is empty or above MAX_INPUT_CHARS
- The cleanup LLM call fails
- The model returns empty text
"""

import logging
from typing import Optional

from app.models.chat import ChatRequest
from app.services.provider_router import ProviderRouter

logger = logging.getLogger("ghostpour.transcript_cleanup")

# Sources we currently know how to clean. "speech_to_text" will be added
# once we have a tuned STT prompt; "mixed" is intentionally excluded
# because picking the wrong cleanup prompt is worse than no cleanup.
_CLEANABLE_SOURCES = {"ocr_captions"}

# Production cleanup model. Picked via eval 2026-05-23: GPT-4.1-mini gives
# ~97% of Sonnet 4.6's ROUGE on captions cleanup at one tenth the cost
# and correct speaker-attribution on the hardest sample case. See
# tests/evals/captions_cleanup/run_eval.py for the eval harness + leaderboard.
_CLEANUP_PROVIDER = "openai"
_CLEANUP_MODEL = "gpt-4.1-mini"

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


async def clean_transcript(
    provider_router: ProviderRouter,
    raw_transcript: str,
    remote_configs: dict,
    transcript_source: str,
    *,
    locale: str = "en",
    meeting_id: str | None = None,
) -> Optional[str]:
    """Run the LLM cleanup pass. Returns the cleaned transcript on success,
    None on any failure (caller falls back to raw and omits the field).
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

    request = ChatRequest(
        provider=_CLEANUP_PROVIDER,
        model=_CLEANUP_MODEL,
        system_prompt=system_prompt,
        user_content=raw_transcript,
        max_tokens=8000,
        call_type="captions_cleanup",
        prompt_mode="CaptionsTranscriptCleanup",
        meeting_id=meeting_id,
    )

    try:
        response = await provider_router.route(request)
    except Exception as e:
        logger.warning(
            "Transcript cleanup failed meeting=%s: %s", meeting_id, e,
        )
        return None

    cleaned = (response.text or "").strip()
    if not cleaned:
        logger.warning(
            "Transcript cleanup returned empty text meeting=%s", meeting_id,
        )
        return None

    ratio = len(cleaned) / len(raw_transcript) if raw_transcript else 0
    logger.info(
        "Transcript cleanup ok meeting=%s source=%s in_chars=%d out_chars=%d ratio=%.2f",
        meeting_id, transcript_source, len(raw_transcript), len(cleaned), ratio,
    )
    return cleaned
