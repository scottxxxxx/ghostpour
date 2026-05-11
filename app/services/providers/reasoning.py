"""Per-provider translation of the normalized ChatRequest.reasoning level.

The ChatRequest exposes a single field — `reasoning` ∈
{default, minimal, low, medium, high} — so callers (SS iOS, future web
clients) get one knob. Each adapter calls the helper for its provider to
merge the right native fields into the outgoing request body.

When `ChatRequest.reasoning` is None, helpers return an empty dict and the
provider's own default applies (legacy clients that omit the field).

Per-model `reasoningLevels` arrays in `model-capabilities.json` drive which
values iOS exposes per model. The wire contract is in
`docs/wire-contracts/reasoning-control.md`.

OpenRouter (called directly by SS, not proxied through CloudZap) exposes
its own unified `reasoning` block — see the same wire-contract doc for the
SS-side mapping that mirrors this file.
"""

from __future__ import annotations

from app.models.chat import ReasoningLevel


# Anthropic budget_tokens by level. Anthropic requires budget_tokens < max_tokens,
# so callers may need to lift max_tokens; see anthropic_min_max_tokens below.
_ANTHROPIC_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}

# Gemini 2.5 thinkingBudget by level (integer field).
# 0 disables thinking on Flash/Flash-Lite; Pro doesn't accept 0.
_GEMINI_25_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}


def openai_compat_fields(
    provider: str, level: ReasoningLevel | None
) -> dict:
    """Fields to merge into an OpenAI-compat request body.

    Provider IDs match config/providers.yml: openai, xai, deepseek, kimi, qwen.
    """
    p = provider.lower()
    is_default = level is None or level == "default"

    if p == "openai":
        # GPT-5 family: reasoning_effort minimal|low|medium|high (all native).
        # Default = omit (provider's own default applies).
        if is_default:
            return {}
        return {"reasoning_effort": level}

    if p == "xai":
        # Grok 4: reasoning_effort low|high natively (no minimal/medium).
        # `model-capabilities.json` should only expose ["low", "high"] for
        # Grok. Default = omit. Defensive collapse for stale clients.
        if is_default:
            return {}
        effort = "low" if level in ("minimal", "low") else "high"
        return {"reasoning_effort": effort}

    if p == "deepseek":
        # V4 dual-mode: explicit thinking block + optional effort.
        # `model-capabilities.json` exposes only ["default", "high"].
        # Default and minimal both force-disable; everything else enables.
        if is_default or level == "minimal":
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": level,  # low/medium map to high server-side
        }

    if p in ("kimi", "qwen"):
        # Boolean toggle. `model-capabilities.json` exposes only
        # ["default", "high"]. Default and minimal both force-disable.
        if is_default or level == "minimal":
            return {"enable_thinking": False}
        return {"enable_thinking": True}

    return {}


def anthropic_thinking_block(level: ReasoningLevel | None) -> dict | None:
    """Returns the thinking block to splice into an Anthropic request, or None.

    Anthropic doesn't have a native "minimal" tier. `model-capabilities.json`
    only exposes ["default", "low", "medium", "high"] for Claude; "minimal"
    on a stale client defensively returns None (same as default).
    """
    if level is None or level == "default" or level == "minimal":
        return None
    return {
        "type": "enabled",
        "budget_tokens": _ANTHROPIC_BUDGET[level],
    }


def anthropic_min_max_tokens(level: ReasoningLevel | None) -> int:
    """Floor for max_tokens when thinking is enabled (budget + 1024 headroom)."""
    if level is None or level == "default" or level == "minimal":
        return 0
    return _ANTHROPIC_BUDGET[level] + 1024


def _is_gemini_3(model: str) -> bool:
    """True for Gemini 3.x model IDs.

    Examples: gemini-3-flash-preview, gemini-3.1-pro-preview,
    gemini-3.1-flash-lite-preview. False for gemini-2.5-* and earlier.
    """
    m = model.lower()
    return m.startswith("gemini-3") or m.startswith("gemini-3.")


def _is_gemini_3_pro(model: str) -> bool:
    """True for Gemini 3 Pro variants (no `minimal` support per Google)."""
    m = model.lower()
    return "3.1-pro" in m or "3-pro" in m


def gemini_thinking_config(
    level: ReasoningLevel | None, model: str
) -> dict | None:
    """Returns the thinkingConfig block for Gemini, or None.

    Dispatches on model family:
      - Gemini 3.x uses `thinkingLevel: minimal|low|medium|high`
        (minimal only on Flash/Flash-Lite; Pro rejects it).
      - Gemini 2.5.x uses `thinkingBudget: <int>` (0 disables on Flash,
        not Pro; no native "minimal" level).
    """
    if level is None or level == "default":
        return None

    if _is_gemini_3(model):
        # Defensive: Pro doesn't accept "minimal" per Google's docs.
        # `model-capabilities.json` should already gate the picker, but if
        # a stale client still sends it, collapse to "low".
        if level == "minimal" and _is_gemini_3_pro(model):
            return {"thinkingLevel": "low"}
        return {"thinkingLevel": level}

    # Gemini 2.5.x — integer budget field.
    if level == "minimal":
        # Flash/Flash-Lite accept thinkingBudget=0 (disable). Pro does not;
        # `model-capabilities.json` shouldn't expose "minimal" on 2.5 Pro.
        return {"thinkingBudget": 0}
    return {"thinkingBudget": _GEMINI_25_BUDGET[level]}
