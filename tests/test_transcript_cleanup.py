"""Tests for app.services.transcript_cleanup + the report/chat integrations.

Covers:
- should_clean() boolean matrix per (source, flag) combination
- get_cleanup_prompt() locale-falls-back to English when missing
- clean_transcript() primary-then-fallback routing:
    * primary success returns primary output (DeepSeek dispatched)
    * primary timeout / error / empty triggers fallback (Haiku dispatched)
    * both failing returns None
- protected-prompts.json carries the transcriptCleanup.ocr_captions key at v10+
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.transcript_cleanup import (
    MAX_INPUT_CHARS,
    clean_transcript,
    get_cleanup_prompt,
    should_clean,
)


# --- should_clean -----------------------------------------------------------


@pytest.mark.parametrize(
    "source,flag,expected",
    [
        ("ocr_captions", True,  True),
        ("ocr_captions", False, False),
        ("speech_to_text", True,  False),  # not yet cleanable
        ("mixed",         True,  False),  # intentionally skipped
        (None,            True,  False),
        ("",              True,  False),
        ("ocr_captions", True,  True),
    ],
)
def test_should_clean(source, flag, expected):
    assert should_clean(source, flag) is expected


# --- get_cleanup_prompt -----------------------------------------------------


def test_get_cleanup_prompt_english():
    configs = {
        "protected-prompts": {
            "transcriptCleanup": {"ocr_captions": "EN PROMPT"},
        },
    }
    assert get_cleanup_prompt(configs, "ocr_captions", locale="en") == "EN PROMPT"


def test_get_cleanup_prompt_locale_falls_back_to_english():
    """When the localized file lacks the field, fall back to English."""
    configs = {
        "protected-prompts": {
            "transcriptCleanup": {"ocr_captions": "EN PROMPT"},
        },
        "protected-prompts.es": {
            # Localized file has no transcriptCleanup section
        },
    }
    assert get_cleanup_prompt(configs, "ocr_captions", locale="es") == "EN PROMPT"


def test_get_cleanup_prompt_locale_preferred_when_present():
    configs = {
        "protected-prompts": {
            "transcriptCleanup": {"ocr_captions": "EN"},
        },
        "protected-prompts.es": {
            "transcriptCleanup": {"ocr_captions": "ES"},
        },
    }
    assert get_cleanup_prompt(configs, "ocr_captions", locale="es") == "ES"


def test_get_cleanup_prompt_returns_none_when_missing():
    configs = {"protected-prompts": {}}
    assert get_cleanup_prompt(configs, "ocr_captions", locale="en") is None


# --- clean_transcript -------------------------------------------------------


_CONFIGS = {
    "protected-prompts": {
        "transcriptCleanup": {"ocr_captions": "Clean this transcript:"},
    },
}


def _ok(text: str):
    """Build a minimal response object the routing layer would return."""
    return SimpleNamespace(text=text)


@pytest.mark.asyncio
async def test_clean_transcript_happy_path_uses_primary_deepseek():
    """Primary returns successfully — fallback should not be invoked."""
    router = AsyncMock()
    router.route.return_value = _ok("PRIMARY OUTPUT")
    result = await clean_transcript(
        router, "raw transcript here", _CONFIGS, "ocr_captions",
        locale="en", meeting_id="m1",
    )
    assert result == "PRIMARY OUTPUT"
    assert router.route.await_count == 1
    sent = router.route.call_args_list[0][0][0]
    assert sent.call_type == "captions_cleanup"
    assert sent.prompt_mode == "CaptionsTranscriptCleanup"
    assert sent.provider == "openrouter"
    assert sent.model == "deepseek/deepseek-v3.2-exp"


@pytest.mark.asyncio
async def test_clean_transcript_returns_none_on_empty_input():
    router = AsyncMock()
    assert await clean_transcript(router, "", _CONFIGS, "ocr_captions") is None
    assert await clean_transcript(router, "   \n  ", _CONFIGS, "ocr_captions") is None
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_clean_transcript_returns_none_on_oversized_input():
    router = AsyncMock()
    huge = "x" * (MAX_INPUT_CHARS + 1)
    assert await clean_transcript(router, huge, _CONFIGS, "ocr_captions") is None
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_clean_transcript_returns_none_when_no_prompt_configured():
    router = AsyncMock()
    configs = {"protected-prompts": {}}
    assert await clean_transcript(router, "raw", configs, "ocr_captions") is None
    router.route.assert_not_called()


# --- Fallback behavior ------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_error_falls_back_to_haiku():
    """Primary raises a generic exception → fallback (Haiku) takes over."""
    router = AsyncMock()
    router.route.side_effect = [RuntimeError("upstream blew up"), _ok("FALLBACK OUTPUT")]
    result = await clean_transcript(
        router, "raw", _CONFIGS, "ocr_captions", meeting_id="m2",
    )
    assert result == "FALLBACK OUTPUT"
    assert router.route.await_count == 2
    primary = router.route.call_args_list[0][0][0]
    fallback = router.route.call_args_list[1][0][0]
    assert primary.provider == "openrouter"
    assert primary.model == "deepseek/deepseek-v3.2-exp"
    assert fallback.provider == "anthropic"
    assert fallback.model == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_primary_timeout_falls_back_to_haiku():
    """asyncio.TimeoutError on primary → fallback fires."""
    router = AsyncMock()
    router.route.side_effect = [asyncio.TimeoutError(), _ok("FALLBACK OUTPUT")]
    result = await clean_transcript(
        router, "raw", _CONFIGS, "ocr_captions", meeting_id="m3",
    )
    assert result == "FALLBACK OUTPUT"
    assert router.route.await_count == 2


@pytest.mark.asyncio
async def test_primary_empty_falls_back_to_haiku():
    """Primary returns empty text → fallback fires (this was the
    cratering DeepSeek run we observed in eval)."""
    router = AsyncMock()
    router.route.side_effect = [_ok("   "), _ok("FALLBACK OUTPUT")]
    result = await clean_transcript(
        router, "raw", _CONFIGS, "ocr_captions", meeting_id="m4",
    )
    assert result == "FALLBACK OUTPUT"
    assert router.route.await_count == 2


@pytest.mark.asyncio
async def test_both_routes_fail_returns_none():
    """Primary errors and fallback also errors → silent skip."""
    router = AsyncMock()
    router.route.side_effect = [RuntimeError("primary down"), RuntimeError("fallback down")]
    result = await clean_transcript(
        router, "raw", _CONFIGS, "ocr_captions", meeting_id="m5",
    )
    assert result is None
    assert router.route.await_count == 2


@pytest.mark.asyncio
async def test_both_routes_empty_returns_none():
    """Both routes return empty text → silent skip."""
    router = AsyncMock()
    router.route.side_effect = [_ok(""), _ok("   ")]
    result = await clean_transcript(
        router, "raw", _CONFIGS, "ocr_captions", meeting_id="m6",
    )
    assert result is None
    assert router.route.await_count == 2


# --- sub-call metering (on_subcall) -----------------------------------------


@pytest.mark.asyncio
async def test_meters_winning_primary_attempt():
    """A successful primary cleanup invokes on_subcall once with the cleanup
    request (call_type=captions_cleanup), the response, and an int elapsed_ms."""
    router = AsyncMock()
    router.route.return_value = _ok("PRIMARY OUTPUT")
    meter = AsyncMock()
    result = await clean_transcript(
        router, "raw transcript here", _CONFIGS, "ocr_captions",
        meeting_id="m1", on_subcall=meter,
    )
    assert result == "PRIMARY OUTPUT"
    meter.assert_awaited_once()
    req, resp, elapsed = meter.await_args[0]
    assert req.get_meta("call_type") == "captions_cleanup"
    assert req.get_meta("meeting_id") == "m1"
    assert req.model == "deepseek/deepseek-v3.2-exp"
    assert resp.text == "PRIMARY OUTPUT"
    assert isinstance(elapsed, int)


@pytest.mark.asyncio
async def test_meters_winning_fallback_attempt():
    """When the primary fails and the fallback wins, metering fires once for
    the fallback call (Haiku), not the dead primary."""
    router = AsyncMock()
    router.route.side_effect = [RuntimeError("primary down"), _ok("FALLBACK OUTPUT")]
    meter = AsyncMock()
    result = await clean_transcript(
        router, "raw transcript here", _CONFIGS, "ocr_captions",
        meeting_id="m2", on_subcall=meter,
    )
    assert result == "FALLBACK OUTPUT"
    meter.assert_awaited_once()
    req, resp, _ = meter.await_args[0]
    assert req.model == "claude-haiku-4-5-20251001"
    assert resp.text == "FALLBACK OUTPUT"


@pytest.mark.asyncio
async def test_metering_failure_does_not_drop_result():
    """A throwing on_subcall must not cost us the cleaned transcript."""
    router = AsyncMock()
    router.route.return_value = _ok("PRIMARY OUTPUT")
    meter = AsyncMock(side_effect=RuntimeError("db write blew up"))
    result = await clean_transcript(
        router, "raw transcript here", _CONFIGS, "ocr_captions",
        meeting_id="m3", on_subcall=meter,
    )
    assert result == "PRIMARY OUTPUT"
    meter.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_metering_callback_when_cleanup_fails():
    """Both routes failing returns None and never calls on_subcall."""
    router = AsyncMock()
    router.route.side_effect = [RuntimeError("primary"), RuntimeError("fallback")]
    meter = AsyncMock()
    result = await clean_transcript(
        router, "raw transcript here", _CONFIGS, "ocr_captions", on_subcall=meter,
    )
    assert result is None
    meter.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_and_log_computes_cost_then_records_and_logs():
    """UsageTracker.record_and_log costs the sub-call from pricing, deducts it,
    and writes the usage_log row — the metering primitive cleanup relies on."""
    from app.services.usage_tracker import UsageTracker

    tracker = UsageTracker()
    tracker.record_cost = AsyncMock()
    tracker.log_usage = AsyncMock()

    pricing = SimpleNamespace(
        is_loaded=True,
        calculate_cost=lambda **kw: {"total_cost": 0.0123},
    )
    response = SimpleNamespace(text="x", usage={}, input_tokens=100, output_tokens=50, cost=None)
    request = SimpleNamespace(provider="openrouter", model="deepseek/deepseek-v3.2-exp")
    user = SimpleNamespace(id="u1")
    tier = SimpleNamespace()

    await tracker.record_and_log(
        db=None, user=user, tier=tier, app_id="shouldersurf",
        request=request, response=response, elapsed_ms=4200, pricing=pricing,
    )

    assert response.cost == {"total_cost": 0.0123}
    tracker.record_cost.assert_awaited_once()
    assert tracker.record_cost.await_args[0][2] == 0.0123      # cost arg
    tracker.log_usage.assert_awaited_once()
    assert tracker.log_usage.await_args.kwargs["app_id"] == "shouldersurf"


# --- protected-prompts.json content lock ------------------------------------


def test_protected_prompts_carries_ocr_cleanup_prompt():
    """The OCR cleanup prompt is the contract between the cleanup module
    and the remote config. If it gets removed in a future edit, cleanup
    silently no-ops because the loader returns None — and the only
    signal would be users not seeing cleaned_transcript come back.
    """
    data = json.loads(Path("config/remote/protected-prompts.json").read_text())
    assert data["version"] >= 10
    assert "transcriptCleanup" in data
    prompt = data["transcriptCleanup"].get("ocr_captions")
    assert prompt and len(prompt) > 100
    # The "RESTRUCTURE, not RECOVER" anti-hallucination clause is the
    # core constraint that prevents the model from inventing dialogue.
    # Removing it caused GPT-4.1-mini to hallucinate a "high level
    # feedback is good" line during eval 2026-05-23.
    assert "RESTRUCTURE" in prompt
    assert "RECOVER" in prompt
