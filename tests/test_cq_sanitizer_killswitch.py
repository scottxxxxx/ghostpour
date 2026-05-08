"""Tests for the (you)-suffix sanitizer kill-switch.

The render-time `_sanitize_you_suffix` regex is a workaround for
historical CQ patches that stored "Name (you)" forms. CQ #43 + #93
tightened extraction-side voice rules so new patches use second-person
"You" natively. The sanitizer should be retiring; setting
CZ_CQ_DISABLE_YOU_SUFFIX_SANITIZER=true on a canary lets us verify the
unsanitized recall path is still grammatical before deleting the regex.

Default (off) keeps today's behavior — sanitizer runs.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatRequest
from app.models.user import UserRecord
from app.services.features.context_quilt_hook import ContextQuiltHook


def _user() -> UserRecord:
    return UserRecord(
        id="u-1",
        apple_sub="apple-u-1",
        tier="pro",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def _request() -> ChatRequest:
    return ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE\n\n{{context_quilt}}",
        user_content="hi",
        context_quilt=True,
    )


@pytest.mark.asyncio
async def test_sanitizer_runs_by_default():
    """Default (CZ_CQ_DISABLE_YOU_SUFFIX_SANITIZER unset) → '(you)' is
    stripped from recall context before injection."""
    raw_recall = "Scott (you) prefers concise answers."
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": raw_recall, "matched_entities": []},
    ):
        new_body, _ = await ContextQuiltHook().before_llm(
            user=_user(),
            body=_request(),
            tier=None,
            feature_state="enabled",
            skip_teasers=set(),
        )
    # Sanitizer rewrites "Scott (you) prefers..." → "You prefers..."
    assert "(you)" not in new_body.system_prompt
    assert "You" in new_body.system_prompt


@pytest.mark.asyncio
async def test_kill_switch_passes_recall_through_unmodified():
    """CZ_CQ_DISABLE_YOU_SUFFIX_SANITIZER=true → raw '(you)' substrings
    survive into system_prompt and the cached recall block. This is the
    canary configuration."""
    raw_recall = "Scott (you) prefers concise answers."
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    try:
        with patch(
            "app.services.features.context_quilt_hook.get_settings",
            return_value=Settings(
                jwt_secret="x" * 32,
                cq_disable_you_suffix_sanitizer=True,
            ),
        ), patch(
            "app.services.features.context_quilt_hook.cq.recall",
            new_callable=AsyncMock,
            return_value={"context": raw_recall, "matched_entities": []},
        ):
            new_body, _ = await ContextQuiltHook().before_llm(
                user=_user(),
                body=_request(),
                tier=None,
                feature_state="enabled",
                skip_teasers=set(),
            )
        assert raw_recall in new_body.system_prompt
    finally:
        get_settings.cache_clear()
