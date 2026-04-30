"""Contract tests for the normalized reasoning level → per-provider mapping."""

from app.services.providers.reasoning import (
    anthropic_min_max_tokens,
    anthropic_thinking_block,
    gemini_thinking_config,
    openai_compat_fields,
)


def test_none_level_yields_no_fields():
    assert openai_compat_fields("openai", None) == {}
    assert openai_compat_fields("deepseek", None) == {}
    assert anthropic_thinking_block(None) is None
    assert gemini_thinking_config(None) is None


def test_openai_levels():
    assert openai_compat_fields("openai", "off") == {"reasoning_effort": "minimal"}
    assert openai_compat_fields("openai", "low") == {"reasoning_effort": "low"}
    assert openai_compat_fields("openai", "medium") == {"reasoning_effort": "medium"}
    assert openai_compat_fields("openai", "high") == {"reasoning_effort": "high"}


def test_xai_collapses_to_low_high():
    assert openai_compat_fields("xai", "off") == {"reasoning_effort": "low"}
    assert openai_compat_fields("xai", "low") == {"reasoning_effort": "low"}
    assert openai_compat_fields("xai", "medium") == {"reasoning_effort": "high"}
    assert openai_compat_fields("xai", "high") == {"reasoning_effort": "high"}


def test_deepseek_dual_mode():
    assert openai_compat_fields("deepseek", "off") == {
        "thinking": {"type": "disabled"}
    }
    for lvl in ("low", "medium", "high"):
        out = openai_compat_fields("deepseek", lvl)
        assert out["thinking"] == {"type": "enabled"}
        assert out["reasoning_effort"] == lvl


def test_kimi_qwen_boolean_toggle():
    for p in ("kimi", "qwen"):
        assert openai_compat_fields(p, "off") == {"enable_thinking": False}
        assert openai_compat_fields(p, "low") == {"enable_thinking": True}
        assert openai_compat_fields(p, "high") == {"enable_thinking": True}


def test_anthropic_thinking_block_off_is_none():
    assert anthropic_thinking_block("off") is None


def test_anthropic_thinking_block_enabled_levels():
    for lvl, budget in (("low", 1024), ("medium", 4096), ("high", 16384)):
        block = anthropic_thinking_block(lvl)
        assert block == {"type": "enabled", "budget_tokens": budget}
        # min_max_tokens leaves headroom above budget for the actual response.
        assert anthropic_min_max_tokens(lvl) == budget + 1024


def test_gemini_thinking_budget_off_zero():
    assert gemini_thinking_config("off") == {"thinkingBudget": 0}
    assert gemini_thinking_config("high") == {"thinkingBudget": 16384}


def test_unknown_provider_returns_empty():
    assert openai_compat_fields("perplexity", "high") == {}
