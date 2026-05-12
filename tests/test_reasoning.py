"""Contract tests for the pass-through reasoning translation.

Each model's `reasoningLevels` array in `model-capabilities.json` contains
the LITERAL native values that provider accepts. iOS picks one and sends
it verbatim as `reasoning`. The adapter's only job is to put that string
into the right native field name on the way out.

No normalized vocabulary, no value mapping, no defensive collapses. If
iOS sends an unsupported value, the provider returns the appropriate
4xx — which is where that error belongs.
"""

from app.services.providers.reasoning import (
    anthropic_output_config,
    anthropic_thinking_block,
    anthropic_uses_effort_path,
    gemini_thinking_config,
    openai_compat_fields,
)


# ---------------------------------------------------------------------------
# None / empty level — omit reasoning fields everywhere
# ---------------------------------------------------------------------------

def test_no_level_omits_reasoning_fields_everywhere():
    """None, empty string, and the literal "default" all omit reasoning
    fields — provider's API default applies."""
    for p in ("openai", "xai", "deepseek", "kimi", "qwen", "unknown"):
        assert openai_compat_fields(p, None) == {}
        assert openai_compat_fields(p, "") == {}
        assert openai_compat_fields(p, "default") == {}
    assert anthropic_thinking_block(None, "claude-opus-4-7") is None
    assert anthropic_thinking_block("", "claude-sonnet-4-6") is None
    assert anthropic_thinking_block("default", "claude-opus-4-7") is None
    assert anthropic_output_config(None, "claude-opus-4-7") is None
    assert anthropic_output_config("default", "claude-opus-4-7") is None
    assert gemini_thinking_config(None, "gemini-3-flash-preview") is None
    assert gemini_thinking_config("default", "gemini-3-flash-preview") is None


# ---------------------------------------------------------------------------
# OpenAI + xAI — pass-through `reasoning_effort`
# ---------------------------------------------------------------------------

def test_openai_passes_through_any_string():
    """OpenAI gpt-5.x picker values vary per model (none/minimal/low/medium/
    high/xhigh) — the adapter doesn't enforce, just passes through. If iOS
    sends a value the model doesn't accept, the API 400s with a clear
    message; that's the right place to handle it."""
    for v in ("none", "minimal", "low", "medium", "high", "xhigh"):
        assert openai_compat_fields("openai", v) == {"reasoning_effort": v}


def test_xai_passes_through_any_string():
    for v in ("none", "low", "medium", "high"):
        assert openai_compat_fields("xai", v) == {"reasoning_effort": v}


# ---------------------------------------------------------------------------
# DeepSeek + Kimi — pass-through into `thinking: {type: <value>}`
# ---------------------------------------------------------------------------

def test_deepseek_thinking_type_pass_through():
    assert openai_compat_fields("deepseek", "disabled") == {"thinking": {"type": "disabled"}}
    assert openai_compat_fields("deepseek", "enabled") == {"thinking": {"type": "enabled"}}


def test_kimi_thinking_type_pass_through():
    assert openai_compat_fields("kimi", "disabled") == {"thinking": {"type": "disabled"}}
    assert openai_compat_fields("kimi", "enabled") == {"thinking": {"type": "enabled"}}


# ---------------------------------------------------------------------------
# Qwen — picker hidden (bool native, not a string vocabulary)
# ---------------------------------------------------------------------------

def test_qwen_omits_reasoning_fields():
    """Qwen's `enable_thinking` is a bool, not a string. Picker is hidden
    in model-capabilities.json (`supportsReasoning: false`). If iOS still
    sends a level for some reason, we omit — the provider's API default
    kicks in."""
    for v in ("low", "high", "enabled"):
        assert openai_compat_fields("qwen", v) == {}


# ---------------------------------------------------------------------------
# Anthropic — model-aware dispatch
# ---------------------------------------------------------------------------

def test_anthropic_effort_path_models():
    """Opus 4.7, Opus 4.6, Sonnet 4.6, Mythos use `thinking: {type: adaptive}`
    + `output_config: {effort: <value>}`. Pass-through."""
    for model in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-mythos-preview"):
        assert anthropic_uses_effort_path(model)
        for v in ("low", "medium", "high", "xhigh", "max"):
            assert anthropic_thinking_block(v, model) == {"type": "adaptive"}
            assert anthropic_output_config(v, model) == {"effort": v}


def test_anthropic_non_effort_models_return_none():
    """Haiku 4.5, legacy 4-5 variants, and 3.x models are not on the effort
    path. Their picker is hidden in model-capabilities."""
    for model in ("claude-haiku-4-5", "claude-opus-4-5", "claude-sonnet-4-5", "claude-3-7-sonnet-20250219"):
        assert not anthropic_uses_effort_path(model)
        assert anthropic_thinking_block("high", model) is None
        assert anthropic_output_config("high", model) is None


# ---------------------------------------------------------------------------
# Gemini — pass-through into `thinkingConfig: {thinkingLevel: <value>}`
# ---------------------------------------------------------------------------

def test_gemini_3_pass_through():
    """Gemini 3.x: pass-through to thinkingLevel. Adapter doesn't enforce
    per-model variants (Pro doesn't accept `minimal`); enforcement is at
    the picker layer via model-capabilities.json."""
    for model in ("gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview"):
        for v in ("minimal", "low", "medium", "high"):
            assert gemini_thinking_config(v, model) == {"thinkingLevel": v}


def test_gemini_25_returns_none():
    """Gemini 2.5.x uses integer thinkingBudget. No 2.5 models in current
    config; would need separate handling if added."""
    assert gemini_thinking_config("low", "gemini-2.5-flash") is None


# ---------------------------------------------------------------------------
# Unknown providers — silently omit
# ---------------------------------------------------------------------------

def test_unknown_provider_returns_empty():
    assert openai_compat_fields("perplexity", "high") == {}
