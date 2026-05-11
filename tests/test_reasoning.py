"""Contract tests for the normalized reasoning level → per-provider mapping.

Vocabulary: `default | minimal | low | medium | high`. Per-model exposure
is driven by `model-capabilities.json.reasoningLevels`; this file pins the
*translation* layer for every (provider, level) combo, including defensive
behavior for levels a model shouldn't expose.
"""

from app.services.providers.reasoning import (
    anthropic_min_max_tokens,
    anthropic_thinking_block,
    gemini_thinking_config,
    openai_compat_fields,
)


# ---------------------------------------------------------------------------
# None (legacy clients that omit the field) — empty / no thinking
# ---------------------------------------------------------------------------

def test_none_level_yields_no_fields_for_omit_providers():
    """OpenAI / xAI omit reasoning fields when level is None (provider default applies)."""
    assert openai_compat_fields("openai", None) == {}
    assert openai_compat_fields("xai", None) == {}
    assert anthropic_thinking_block(None) is None
    assert gemini_thinking_config(None, "gemini-3-flash-preview") is None


def test_none_level_force_disables_on_binary_providers():
    """Kimi/Qwen/DeepSeek: absence of an explicit level still force-disables thinking.

    For these providers, an absent `enable_thinking` / `thinking` field
    means the provider's default kicks in — which on these specific
    providers is typically thinking-on. We default-to-cheapest by
    force-disabling at the field level."""
    assert openai_compat_fields("kimi", None) == {"enable_thinking": False}
    assert openai_compat_fields("qwen", None) == {"enable_thinking": False}
    assert openai_compat_fields("deepseek", None) == {"thinking": {"type": "disabled"}}


# ---------------------------------------------------------------------------
# `default` — same shape as None on each provider
# ---------------------------------------------------------------------------

def test_default_level_matches_none():
    for p in ("openai", "xai"):
        assert openai_compat_fields(p, "default") == openai_compat_fields(p, None) == {}
    for p in ("kimi", "qwen"):
        assert openai_compat_fields(p, "default") == openai_compat_fields(p, None) == {
            "enable_thinking": False
        }
    assert openai_compat_fields("deepseek", "default") == {"thinking": {"type": "disabled"}}
    assert anthropic_thinking_block("default") is None
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
# xAI Grok — 2-level (low, high); collapses minimal/medium defensively
# ---------------------------------------------------------------------------

def test_xai_collapses_to_low_high():
    # model-capabilities.json should only expose ["low", "high"] for Grok,
    # but defensive collapse covers stale clients:
    assert openai_compat_fields("xai", "minimal") == {"reasoning_effort": "low"}
    assert openai_compat_fields("xai", "low") == {"reasoning_effort": "low"}
    assert openai_compat_fields("xai", "medium") == {"reasoning_effort": "high"}
    assert openai_compat_fields("xai", "high") == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# DeepSeek V4 — dual-mode (thinking on/off + optional effort)
# ---------------------------------------------------------------------------

def test_deepseek_minimal_disables_thinking():
    """Even though model-capabilities.json should expose only ["default", "high"]
    for DeepSeek, "minimal" from a stale client maps to disable."""
    assert openai_compat_fields("deepseek", "minimal") == {"thinking": {"type": "disabled"}}


def test_deepseek_enabled_levels():
    for lvl in ("low", "medium", "high"):
        out = openai_compat_fields("deepseek", lvl)
        assert out["thinking"] == {"type": "enabled"}
        assert out["reasoning_effort"] == lvl


# ---------------------------------------------------------------------------
# Kimi / Qwen — boolean toggle
# ---------------------------------------------------------------------------

def test_kimi_qwen_boolean_toggle():
    for p in ("kimi", "qwen"):
        # minimal is hidden from picker but stale clients defensively disable:
        assert openai_compat_fields(p, "minimal") == {"enable_thinking": False}
        # any explicit non-default level enables:
        assert openai_compat_fields(p, "low") == {"enable_thinking": True}
        assert openai_compat_fields(p, "medium") == {"enable_thinking": True}
        assert openai_compat_fields(p, "high") == {"enable_thinking": True}


# ---------------------------------------------------------------------------
# Anthropic Claude — no minimal, thinking block with budget
# ---------------------------------------------------------------------------

def test_anthropic_minimal_is_no_block():
    """Anthropic doesn't expose minimal in the picker; defensively returns
    no thinking block (same as default) if a stale client sends it."""
    assert anthropic_thinking_block("minimal") is None


def test_anthropic_enabled_levels():
    for lvl, budget in (("low", 1024), ("medium", 4096), ("high", 16384)):
        block = anthropic_thinking_block(lvl)
        assert block == {"type": "enabled", "budget_tokens": budget}
        # min_max_tokens leaves headroom above budget for the actual response.
        assert anthropic_min_max_tokens(lvl) == budget + 1024


# ---------------------------------------------------------------------------
# Gemini 3.x — thinkingLevel string
# ---------------------------------------------------------------------------

def test_gemini_3_flash_supports_all_levels_including_minimal():
    """Gemini 3 Flash supports thinkingLevel=minimal natively per Google."""
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
    """Per Google: Gemini 3 Pro does NOT accept thinkingLevel=minimal.
    model-capabilities.json should hide the button; defensive collapse
    catches stale clients."""
    assert gemini_thinking_config("minimal", "gemini-3.1-pro-preview") == {
        "thinkingLevel": "low"
    }
    # Non-minimal levels pass through unchanged.
    for lvl in ("low", "medium", "high"):
        assert gemini_thinking_config(lvl, "gemini-3.1-pro-preview") == {
            "thinkingLevel": lvl
        }


# ---------------------------------------------------------------------------
# Gemini 2.5.x — thinkingBudget int (defensive — no 2.5 models in current
# model-capabilities.json, but adapter dispatches by model family so the
# mapping needs to exist for future adds)
# ---------------------------------------------------------------------------

def test_gemini_25_flash_minimal_is_zero_budget():
    """Gemini 2.5 Flash supports thinkingBudget=0 (disable thinking)."""
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
