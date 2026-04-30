"""Per-provider translation of the normalized ChatRequest.reasoning level.

The ChatRequest exposes a single field — `reasoning` ∈ {off, low, medium, high}
— so callers (SS iOS, future web clients) get one knob. Each adapter calls
the helper for its provider to merge the right native fields into the
outgoing request body.

When ChatRequest.reasoning is None, helpers return an empty dict and the
provider's own default applies.

OpenRouter (called directly by SS, not proxied through CloudZap) exposes its
own unified `reasoning` block — see docs/handoffs/ss-reasoning-control.md
for the SS-side mapping that mirrors this file.
"""

from __future__ import annotations

from app.models.chat import ReasoningLevel


# Anthropic budget_tokens by level. Anthropic requires budget_tokens < max_tokens,
# so callers may need to lift max_tokens; see _anthropic_thinking_block below.
_ANTHROPIC_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}

# Gemini thinkingBudget by level. 0 disables thinking on 2.5+ models.
_GEMINI_BUDGET = {"off": 0, "low": 1024, "medium": 4096, "high": 16384}


def openai_compat_fields(
    provider: str, level: ReasoningLevel | None
) -> dict:
    """Fields to merge into an OpenAI-compat request body.

    Provider IDs match config/providers.yml: openai, xai, deepseek, kimi, qwen.
    """
    if level is None:
        return {}

    p = provider.lower()

    if p == "openai":
        # GPT-5 family: reasoning_effort minimal|low|medium|high
        effort = "minimal" if level == "off" else level
        return {"reasoning_effort": effort}

    if p == "xai":
        # Grok 4: reasoning_effort low|high (no minimal/medium — collapse).
        effort = "low" if level in ("off", "low") else "high"
        return {"reasoning_effort": effort}

    if p == "deepseek":
        # V4 dual-mode: explicit thinking block + optional effort.
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": level,  # low/medium map to high server-side
        }

    if p in ("kimi", "qwen"):
        # Boolean toggle on Kimi K2.5 and Qwen 3.5.
        return {"enable_thinking": level != "off"}

    return {}


def anthropic_thinking_block(level: ReasoningLevel | None) -> dict | None:
    """Returns the thinking block to splice into an Anthropic request, or None."""
    if level is None or level == "off":
        return None
    return {
        "type": "enabled",
        "budget_tokens": _ANTHROPIC_BUDGET[level],
    }


def anthropic_min_max_tokens(level: ReasoningLevel | None) -> int:
    """Floor for max_tokens when thinking is enabled (budget + 1024 headroom)."""
    if level is None or level == "off":
        return 0
    return _ANTHROPIC_BUDGET[level] + 1024


def gemini_thinking_config(level: ReasoningLevel | None) -> dict | None:
    """Returns the thinkingConfig block for Gemini, or None."""
    if level is None:
        return None
    return {"thinkingBudget": _GEMINI_BUDGET[level]}
