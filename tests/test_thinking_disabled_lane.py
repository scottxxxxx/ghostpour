"""GP-controlled `thinking: "disabled"` for short-output lanes.

Sonnet 5 and the Opus 5 family think by default when the request omits the
thinking field, and `max_tokens` is a hard cap on thinking PLUS the reply.
tr_counterpart_turn caps at 300 tokens, so adaptive thinking can eat the
budget and truncate a line of live rehearsal dialogue mid-sentence. The lane
has to say "disabled" explicitly; omitting is no longer equivalent.

Everywhere else omission already means no thinking, so we keep omitting
rather than sending a field older models never needed.
"""

import json

from app.models.chat import ChatRequest
from app.services.prompt_assembly import assemble_prompt
from app.services.providers.reasoning import (
    anthropic_accepts_disabled_thinking,
    anthropic_thinking_block,
    anthropic_uses_effort_path,
)


def _body(**kw):
    """Build the outgoing Anthropic body for a minimal request."""
    from tests.test_anthropic_cache_split import _adapter

    req = ChatRequest(
        provider="anthropic", system_prompt="base", user_content="hi", **kw
    )
    body, _ = _adapter()._build_body(req)
    return body


# --- outgoing body -------------------------------------------------------

def test_body_carries_disabled_block_on_sonnet_5():
    body = _body(model="claude-sonnet-5", thinking="disabled")
    assert body["thinking"] == {"type": "disabled"}


def test_body_omits_thinking_on_models_that_dont_need_it():
    body = _body(model="claude-sonnet-4-6", thinking="disabled")
    assert "thinking" not in body


def test_disabled_thinking_leaves_temperature_free():
    # A disabled block is truthy but doesn't constrain sampling, so it must
    # not suppress temperature the way an adaptive block does.
    body = _body(model="claude-sonnet-5", thinking="disabled", temperature=0.2)
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.2


def test_adaptive_thinking_still_suppresses_temperature():
    body = _body(model="claude-sonnet-5", reasoning="high", temperature=0.2)
    assert body["thinking"] == {"type": "adaptive"}
    assert "temperature" not in body


# --- helper semantics ----------------------------------------------------

def test_disabled_only_where_omitting_would_leave_thinking_on():
    assert anthropic_accepts_disabled_thinking("claude-sonnet-5")
    assert anthropic_accepts_disabled_thinking("claude-opus-5")
    # Omission already means no thinking on these, so we omit instead.
    assert not anthropic_accepts_disabled_thinking("claude-sonnet-4-6")
    assert not anthropic_accepts_disabled_thinking("claude-haiku-4-5-20251001")
    # Explicit disabled is a 400 on the always-thinking models.
    assert not anthropic_accepts_disabled_thinking("claude-fable-5")
    assert not anthropic_accepts_disabled_thinking("claude-mythos-5")


def test_disabled_flag_emits_block_and_wins_over_level():
    assert anthropic_thinking_block(None, "claude-sonnet-5", disabled=True) == {
        "type": "disabled"
    }
    # A lane that declares itself thinking-free stays that way even if a
    # reasoning level rides along on the request.
    assert anthropic_thinking_block("high", "claude-sonnet-5", disabled=True) == {
        "type": "disabled"
    }
    # Models that don't need the explicit block get nothing, not a 400 risk.
    assert anthropic_thinking_block(None, "claude-fable-5", disabled=True) is None
    assert anthropic_thinking_block(None, "claude-haiku-4-5", disabled=True) is None


def test_default_paths_unchanged():
    assert anthropic_thinking_block(None, "claude-sonnet-5") is None
    assert anthropic_thinking_block("default", "claude-sonnet-5") is None
    assert anthropic_thinking_block("high", "claude-sonnet-5") == {"type": "adaptive"}


def test_sonnet_5_is_on_the_effort_path():
    # Missing until 2026-07-29: a picked reasoning level silently did
    # nothing on what is now the primary lane for both apps.
    assert anthropic_uses_effort_path("anthropic/claude-sonnet-5")
    assert anthropic_uses_effort_path("claude-sonnet-4-6")
    assert not anthropic_uses_effort_path("claude-haiku-4-5-20251001")


# --- config -> assembly plumbing -----------------------------------------

def test_counterpart_turn_config_disables_thinking():
    cfg = json.load(open("config/remote/techrehearsal/counterpart-turn.json"))
    assert cfg["thinking"] == "disabled"
    assert cfg["maxTokens"] == 300, "the whole reason the lane needs this"
    assembled = assemble_prompt(
        "tr_counterpart_turn", "BRIEF",
        {"techrehearsal/counterpart-turn": cfg},
        scenario_kind="jobInterview",
    )
    assert assembled["thinking"] == "disabled"
    assert assembled["max_tokens"] == 300


def test_thinking_absent_when_config_omits_it():
    cfg = json.load(open("config/remote/techrehearsal/mock-interview.json"))
    assert "thinking" not in cfg
    assembled = assemble_prompt(
        "tr_mock_interview", "DATA", {"techrehearsal/mock-interview": cfg}
    )
    assert "thinking" not in assembled


def test_chat_request_carries_the_field():
    base = dict(provider="anthropic", model="claude-sonnet-5",
                system_prompt="S", user_content="U")
    assert ChatRequest(**base, thinking="disabled").thinking == "disabled"
    assert ChatRequest(**base).thinking is None
