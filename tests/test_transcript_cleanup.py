"""Tests for app.services.transcript_cleanup + the report/chat integrations.

Covers:
- should_clean() returns the right boolean for each (source, flag) combination
- get_cleanup_prompt() reads from remote_configs and locale-falls-back to English
- clean_transcript() returns None on the expected failure modes (no prompt,
  empty input, oversized input, provider error, empty model response)
- protected-prompts.json carries the transcriptCleanup.ocr_captions key at v10+
"""

from __future__ import annotations

import json
from pathlib import Path
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


@pytest.mark.asyncio
async def test_clean_transcript_happy_path():
    router = AsyncMock()
    router.route.return_value.text = "CLEANED OUTPUT"
    result = await clean_transcript(
        router, "raw transcript here", _CONFIGS, "ocr_captions",
        locale="en", meeting_id="m1",
    )
    assert result == "CLEANED OUTPUT"
    router.route.assert_awaited_once()
    # The dispatched ChatRequest must carry the cleanup call_type so
    # usage_log can attribute the call correctly.
    sent_request = router.route.call_args[0][0]
    assert sent_request.call_type == "captions_cleanup"
    assert sent_request.prompt_mode == "CaptionsTranscriptCleanup"
    assert sent_request.provider == "openai"
    assert sent_request.model == "gpt-4.1-mini"


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
    configs = {"protected-prompts": {}}  # no transcriptCleanup section
    assert await clean_transcript(router, "raw", configs, "ocr_captions") is None
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_clean_transcript_returns_none_on_provider_error():
    router = AsyncMock()
    router.route.side_effect = RuntimeError("upstream timeout")
    assert await clean_transcript(router, "raw", _CONFIGS, "ocr_captions") is None


@pytest.mark.asyncio
async def test_clean_transcript_returns_none_on_empty_response():
    router = AsyncMock()
    router.route.return_value.text = "   "
    assert await clean_transcript(router, "raw", _CONFIGS, "ocr_captions") is None


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
