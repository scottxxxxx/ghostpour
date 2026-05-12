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


# Anthropic budget_tokens by level — used for Haiku 4.5, which doesn't
# support the `effort` parameter and stays on the legacy
# `thinking: {type: enabled, budget_tokens: N}` path. Anthropic requires
# budget_tokens < max_tokens, so callers may need to lift max_tokens; see
# anthropic_min_max_tokens below.
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
        # Grok 4 / 4.1-fast: reasoning_effort none|low|medium|high natively.
        # `model-capabilities.json` exposes [default, low, medium, high].
        # `minimal` defensively collapses to "low" for stale clients.
        if is_default:
            return {}
        if level == "minimal":
            return {"reasoning_effort": "low"}
        return {"reasoning_effort": level}

    if p == "deepseek":
        # V4 dual-mode: `thinking: {type: enabled|disabled}` + optional effort.
        # `model-capabilities.json` exposes only ["default", "high"].
        # Default and minimal both force-disable.
        if is_default or level == "minimal":
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": level,  # low/medium map to high server-side
        }

    if p == "kimi":
        # Kimi K2.x: same `thinking: {type: enabled|disabled}` shape as DeepSeek
        # (NOT `enable_thinking: bool` — that was wrong in the older code).
        # `model-capabilities.json` exposes only ["default", "high"].
        # Default and minimal both force-disable.
        if is_default or level == "minimal":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}

    if p == "qwen":
        # Qwen 3.x via DashScope OpenAI-compatible endpoint: top-level
        # `enable_thinking: bool`. Verified against
        # help.aliyun.com/zh/model-studio/deep-thinking on 2026-05-11:
        #   {"model": "qwen-plus", "enable_thinking": true}
        # (`extra_body` is only needed when using the Python OpenAI SDK
        # because it strips non-standard fields — our adapter builds JSON
        # directly so top-level is correct.)
        #
        # `thinking_budget: int` is also supported for granular control
        # but `model-capabilities.json` exposes only ["default", "high"]
        # so we use the binary toggle. Expand if/when the picker grows.
        if is_default or level == "minimal":
            return {"enable_thinking": False}
        return {"enable_thinking": True}

    return {}


# ---------------------------------------------------------------------------
# Anthropic — model-aware dispatch
# ---------------------------------------------------------------------------
#
# Anthropic has TWO request shapes for controlling thinking depth:
#
#   1. Legacy "manual thinking" — `thinking: {type: enabled, budget_tokens: N}`.
#      Still works on Haiku 4.5 (its only path); deprecated on Sonnet 4.6;
#      REJECTED with 400 on Opus 4.7.
#
#   2. Modern "effort" path — `output_config: {effort: low|medium|high|...}`
#      with `thinking: {type: adaptive}`. Required on Opus 4.7; recommended
#      on Sonnet 4.6 (replaces the deprecated budget_tokens path); NOT
#      supported on Haiku 4.5.
#
# Verified against https://platform.claude.com/docs/en/docs/build-with-claude/effort
# and .../extended-thinking on 2026-05-11. Per the effort doc:
#   "The effort parameter is supported by Claude Mythos Preview, Claude Opus 4.7,
#    Claude Opus 4.6, Claude Sonnet 4.6, and Claude Opus 4.5."
# Haiku 4.5 is explicitly NOT in that list.


def anthropic_uses_effort_path(model: str) -> bool:
    """True if the model accepts `output_config: {effort: ...}`.

    Sonnet 4.6 + Opus 4.7 (and Opus 4.5/4.6/Mythos for completeness). Haiku
    stays on the legacy budget_tokens path.
    """
    m = model.lower()
    return (
        "sonnet-4" in m
        or "opus-4" in m
        or "mythos" in m
    )


def anthropic_thinking_block(
    level: ReasoningLevel | None, model: str | None = None
) -> dict | None:
    """Returns the `thinking` block for an Anthropic request, or None.

    For models on the effort path (Sonnet/Opus 4.x): returns
    `{type: "adaptive"}` for any non-default level so the model uses
    adaptive thinking; `None` for default (no thinking).

    For Haiku 4.5: returns the legacy
    `{type: "enabled", budget_tokens: N}` shape — the only supported path.

    For backwards-compat when called without `model` (older test paths or
    a future adapter that hasn't migrated), assumes the legacy budget path.
    """
    if level is None or level == "default" or level == "minimal":
        return None

    if model and anthropic_uses_effort_path(model):
        return {"type": "adaptive"}

    return {
        "type": "enabled",
        "budget_tokens": _ANTHROPIC_BUDGET[level],
    }


def anthropic_output_config(
    level: ReasoningLevel | None, model: str
) -> dict | None:
    """Returns the `output_config` block for Anthropic effort-path models,
    or None for legacy / default cases.

    Maps our normalized levels onto the effort vocabulary:
      default → omit (Anthropic's default = "high")
      low     → effort: "low"
      medium  → effort: "medium"
      high    → effort: "high"

    minimal collapses to "low" defensively — `model-capabilities.json`
    shouldn't expose minimal on Anthropic models but a stale client
    sending it shouldn't 400."""
    if not anthropic_uses_effort_path(model):
        return None
    if level is None or level == "default":
        return None
    effort = "low" if level == "minimal" else level
    return {"effort": effort}


def anthropic_min_max_tokens(level: ReasoningLevel | None) -> int:
    """Floor for max_tokens when LEGACY thinking is enabled (Haiku path only).

    The effort path doesn't constrain max_tokens — only budget_tokens
    needs to stay below max_tokens. Callers using the effort path can
    skip this lift.
    """
    if level is None or level == "default" or level == "minimal":
        return 0
    return _ANTHROPIC_BUDGET[level] + 1024


# ---------------------------------------------------------------------------
# Gemini — model-aware dispatch (3.x uses thinkingLevel; 2.5.x uses thinkingBudget)
# ---------------------------------------------------------------------------


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
        not Pro; no native "minimal" level — minimal maps to 0 on Flash).
    """
    if level is None or level == "default":
        return None

    if _is_gemini_3(model):
        # Defensive: Pro doesn't accept "minimal" per Google's docs.
        # `model-capabilities.json` already gates the picker, but if a
        # stale client still sends it, collapse to "low".
        if level == "minimal" and _is_gemini_3_pro(model):
            return {"thinkingLevel": "low"}
        return {"thinkingLevel": level}

    # Gemini 2.5.x — integer budget field.
    if level == "minimal":
        # Flash/Flash-Lite accept thinkingBudget=0 (disable). Pro does not;
        # `model-capabilities.json` shouldn't expose "minimal" on 2.5 Pro.
        return {"thinkingBudget": 0}
    return {"thinkingBudget": _GEMINI_25_BUDGET[level]}
