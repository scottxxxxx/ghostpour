"""Contract tests for the normalized reasoning level → per-provider mapping.

Vocabulary: `default | minimal | low | medium | high`. Per-model exposure
is driven by `model-capabilities.json.reasoningLevels`; this file pins the
*translation* layer for every (provider, level) combo, including defensive
behavior for levels a model shouldn't expose.

Verified against provider docs on 2026-05-11:
- Anthropic effort doc: Opus 4.7 requires effort path; Haiku 4.5 NOT in
  effort-supported list (legacy budget_tokens only); Sonnet 4.6 supports
  both (effort recommended).
- Kimi docs: thinking field is `{type: "enabled"/"disabled"}`, not
  `enable_thinking: bool`.
- Qwen / OpenRouter docs: Qwen uses `thinking_budget` (int).
- xAI Grok docs: 4 levels natively (none, low, medium, high).
"""

from app.services.providers.reasoning import (
    anthropic_min_max_tokens,
    anthropic_output_config,
    anthropic_thinking_block,
    anthropic_uses_effort_path,
    gemini_thinking_config,
    openai_compat_fields,
)


# ---------------------------------------------------------------------------
# None (legacy clients that omit the field) — empty / no thinking
# ---------------------------------------------------------------------------

def test_none_level_yields_no_fields_for_omit_providers():
    """OpenAI / xAI omit reasoning fields when level is None."""
    assert openai_compat_fields("openai", None) == {}
    assert openai_compat_fields("xai", None) == {}
    assert anthropic_thinking_block(None, "claude-haiku-4-5") is None
    assert anthropic_thinking_block(None, "claude-opus-4-7") is None
    assert gemini_thinking_config(None, "gemini-3-flash-preview") is None


def test_none_level_force_disables_on_binary_providers():
    """Kimi/DeepSeek: force-disable thinking. Qwen: thinking_budget=0."""
    assert openai_compat_fields("kimi", None) == {"thinking": {"type": "disabled"}}
    assert openai_compat_fields("qwen", None) == {"thinking_budget": 0}
    assert openai_compat_fields("deepseek", None) == {"thinking": {"type": "disabled"}}


# ---------------------------------------------------------------------------
# `default` — same shape as None on each provider
# ---------------------------------------------------------------------------

def test_default_level_matches_none():
    for p in ("openai", "xai"):
        assert openai_compat_fields(p, "default") == openai_compat_fields(p, None) == {}
    assert openai_compat_fields("kimi", "default") == {"thinking": {"type": "disabled"}}
    assert openai_compat_fields("qwen", "default") == {"thinking_budget": 0}
    assert openai_compat_fields("deepseek", "default") == {"thinking": {"type": "disabled"}}
    assert anthropic_thinking_block("default", "claude-haiku-4-5") is None
    assert anthropic_thinking_block("default", "claude-opus-4-7") is None
    assert anthropic_output_config("default", "claude-opus-4-7") is None
    assert gemini_thinking_config("default", "gemini-3-flash-preview") is None


# ---------------------------------------------------------------------------
# OpenAI gpt-5.x — full 4-level support including `minimal`
# ---------------------------------------------------------------------------

def test_openai_levels_all_native():
    assert openai_compat_fields("openai", "minimal") == {"reasoning_effort": "minimal"}
    assert openai_compat_fields("openai", "low") == {"reasoning_effort": "low"}
    assert openai_compat_fields("openai", "medium") == {"reasoning_effort": "medium"}
    assert openai_compat_fields("openai", "high") == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# xAI Grok — 4 levels native; minimal defensively collapses to low
# ---------------------------------------------------------------------------

def test_xai_grok_native_levels():
    """xAI Grok natively supports low/medium/high via reasoning_effort."""
    assert openai_compat_fields("xai", "minimal") == {"reasoning_effort": "low"}
    assert openai_compat_fields("xai", "low") == {"reasoning_effort": "low"}
    assert openai_compat_fields("xai", "medium") == {"reasoning_effort": "medium"}
    assert openai_compat_fields("xai", "high") == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# DeepSeek V4 — dual-mode (thinking on/off + optional effort)
# ---------------------------------------------------------------------------

def test_deepseek_minimal_disables_thinking():
    assert openai_compat_fields("deepseek", "minimal") == {"thinking": {"type": "disabled"}}


def test_deepseek_enabled_levels():
    for lvl in ("low", "medium", "high"):
        out = openai_compat_fields("deepseek", lvl)
        assert out["thinking"] == {"type": "enabled"}
        assert out["reasoning_effort"] == lvl


# ---------------------------------------------------------------------------
# Kimi K2.x — `thinking: {type: "enabled"/"disabled"}` (NOT enable_thinking)
# ---------------------------------------------------------------------------

def test_kimi_uses_thinking_block_not_enable_thinking_bool():
    """Verified against platform.kimi.ai/docs/api/chat on 2026-05-11:
    Kimi K2.5 + K2-Thinking accept `thinking: {type: "enabled"/"disabled"}`."""
    assert openai_compat_fields("kimi", "minimal") == {"thinking": {"type": "disabled"}}
    assert openai_compat_fields("kimi", "high") == {"thinking": {"type": "enabled"}}


def test_kimi_low_medium_also_enable_thinking():
    for lvl in ("low", "medium", "high"):
        assert openai_compat_fields("kimi", lvl) == {"thinking": {"type": "enabled"}}


# ---------------------------------------------------------------------------
# Qwen 3.x — integer `thinking_budget` (NOT enable_thinking)
# ---------------------------------------------------------------------------

def test_qwen_uses_thinking_budget_int():
    """Verified against OpenRouter's translation table on 2026-05-11:
    'Alibaba Qwen models map [max_tokens] to thinking_budget.'"""
    assert openai_compat_fields("qwen", "minimal") == {"thinking_budget": 0}
    assert openai_compat_fields("qwen", "low") == {"thinking_budget": 1024}
    assert openai_compat_fields("qwen", "medium") == {"thinking_budget": 4096}
    assert openai_compat_fields("qwen", "high") == {"thinking_budget": 16384}


# ---------------------------------------------------------------------------
# Anthropic — model-aware dispatch (effort path vs legacy budget_tokens)
# ---------------------------------------------------------------------------

def test_anthropic_haiku_uses_legacy_budget_path():
    """Haiku 4.5 is NOT in Anthropic's effort-supported list (per docs).
    Stays on `thinking: {type: enabled, budget_tokens: N}`."""
    assert not anthropic_uses_effort_path("claude-haiku-4-5")
    block = anthropic_thinking_block("high", "claude-haiku-4-5")
    assert block == {"type": "enabled", "budget_tokens": 16384}
    assert anthropic_output_config("high", "claude-haiku-4-5") is None


def test_anthropic_haiku_max_tokens_lift():
    """Anthropic requires budget_tokens < max_tokens. Helper computes the lift."""
    assert anthropic_min_max_tokens("low") == 2048
    assert anthropic_min_max_tokens("medium") == 5120
    assert anthropic_min_max_tokens("high") == 17408
    assert anthropic_min_max_tokens("default") == 0
    assert anthropic_min_max_tokens("minimal") == 0
    assert anthropic_min_max_tokens(None) == 0


def test_anthropic_opus_uses_effort_path():
    """Opus 4.7 REQUIRES effort path. Manual thinking returns 400 per docs."""
    assert anthropic_uses_effort_path("claude-opus-4-7")
    assert anthropic_thinking_block("medium", "claude-opus-4-7") == {"type": "adaptive"}
    assert anthropic_output_config("medium", "claude-opus-4-7") == {"effort": "medium"}


def test_anthropic_sonnet_uses_effort_path():
    """Sonnet 4.6 supports both paths; we use effort (the recommended path)."""
    assert anthropic_uses_effort_path("claude-sonnet-4-6")
    assert anthropic_thinking_block("low", "claude-sonnet-4-6") == {"type": "adaptive"}
    assert anthropic_output_config("low", "claude-sonnet-4-6") == {"effort": "low"}


def test_anthropic_effort_path_all_levels():
    for lvl in ("low", "medium", "high"):
        assert anthropic_output_config(lvl, "claude-opus-4-7") == {"effort": lvl}
        assert anthropic_thinking_block(lvl, "claude-opus-4-7") == {"type": "adaptive"}


def test_anthropic_minimal_collapses_to_low_on_effort_path():
    """Anthropic effort doesn't have "minimal"; collapse defensively."""
    assert anthropic_output_config("minimal", "claude-opus-4-7") == {"effort": "low"}


def test_anthropic_minimal_is_no_thinking_on_haiku():
    """On the legacy path, minimal is treated as no thinking (same as default)."""
    assert anthropic_thinking_block("minimal", "claude-haiku-4-5") is None


# ---------------------------------------------------------------------------
# Gemini 3.x — thinkingLevel string
# ---------------------------------------------------------------------------

def test_gemini_3_flash_supports_all_levels_including_minimal():
    for lvl in ("minimal", "low", "medium", "high"):
        assert gemini_thinking_config(lvl, "gemini-3-flash-preview") == {
            "thinkingLevel": lvl
        }


def test_gemini_3_flash_lite_supports_minimal():
    for lvl in ("minimal", "low", "medium", "high"):
        assert gemini_thinking_config(lvl, "gemini-3.1-flash-lite-preview") == {
            "thinkingLevel": lvl
        }


def test_gemini_3_pro_collapses_minimal_to_low():
    assert gemini_thinking_config("minimal", "gemini-3.1-pro-preview") == {
        "thinkingLevel": "low"
    }
    for lvl in ("low", "medium", "high"):
        assert gemini_thinking_config(lvl, "gemini-3.1-pro-preview") == {
            "thinkingLevel": lvl
        }


# ---------------------------------------------------------------------------
# Gemini 2.5.x — thinkingBudget int
# ---------------------------------------------------------------------------

def test_gemini_25_flash_minimal_is_zero_budget():
    assert gemini_thinking_config("minimal", "gemini-2.5-flash") == {
        "thinkingBudget": 0
    }


def test_gemini_25_levels_use_integer_budget():
    for lvl, budget in (("low", 1024), ("medium", 4096), ("high", 16384)):
        assert gemini_thinking_config(lvl, "gemini-2.5-flash") == {
            "thinkingBudget": budget
        }


# ---------------------------------------------------------------------------
# Unknown provider — empty (omit)
# ---------------------------------------------------------------------------

def test_unknown_provider_returns_empty():
    assert openai_compat_fields("perplexity", "high") == {}
