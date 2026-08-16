"""Per-provider translation of ChatRequest.reasoning to provider-native fields.

The `reasoning` field on the wire carries the literal native value from the
model's `reasoningLevels` array in `model-capabilities.json`. iOS picks one
of those values and sends it verbatim; this module just slots it into the
right native field name on the way out to each provider.

No normalization, no value mapping, no defensive collapses — if iOS sends
something a provider doesn't accept, the provider returns the appropriate
4xx and that's the right place to surface the error.

The one universal value is **`"default"`** (or empty/None): treated as
"omit the reasoning field, let the provider's API default apply." It's
the first entry in every reasoning-enabled model's `reasoningLevels`
array; iOS shows it as the pre-selected default button.

Per-provider native field placement:

  | Provider | Field on the wire                            | Picker values                                    |
  |----------|----------------------------------------------|--------------------------------------------------|
  | OpenAI   | `reasoning_effort: <value>`                  | gpt-5.5/5.2: none/low/medium/high/xhigh          |
  |          |                                              | gpt-5-mini/nano: minimal/low/medium/high         |
  | xAI Grok | `reasoning_effort: <value>`                  | none/low/medium/high                             |
  | DeepSeek | `thinking: {type: <value>}`                  | disabled / enabled                               |
  | Kimi     | `thinking: {type: <value>}`                  | disabled / enabled                               |
  | Qwen     | (picker hidden — `enable_thinking` is bool)  | n/a                                              |
  | Anthropic Opus 5 / 4.8 / 4.7 / 4.6, Sonnet 5 / 4.6 (effort) | `thinking: {type: "adaptive"}` + `output_config: {effort: <value>}` |
  | Anthropic Haiku 4.5                          | (picker hidden — manual `budget_tokens: int`)    |
  | Google Gemini 3.x                            | `thinkingConfig: {thinkingLevel: <value>}`       |
  | Google Gemini 2.5.x (no config models today) | (would be `thinkingBudget: int`)                 |
"""

from __future__ import annotations


def openai_compat_fields(provider: str, level: str | None) -> dict:
    """Fields to merge into an OpenAI-compatible request body.

    Provider IDs match config/providers.yml: openai, xai, deepseek, kimi.
    Qwen's picker is hidden (bool field) so this branch returns {}.
    """
    if not level or level == "default":
        return {}

    p = provider.lower()

    if p in ("openai", "xai"):
        return {"reasoning_effort": level}

    if p in ("deepseek", "kimi"):
        return {"thinking": {"type": level}}

    # qwen and unknown providers: omit
    return {}


def anthropic_uses_effort_path(model: str) -> bool:
    """True for the modern 4.6+ family (Opus 4.6/4.7, Sonnet 4.6, Mythos).

    These accept `output_config: {effort: ...}` + `thinking: {type:
    "adaptive"}`. Haiku 4.5 is NOT on this path (legacy budget_tokens
    only — and we don't expose it in the picker).
    """
    m = model.lower()
    explicit_effort_models = (
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        # Sonnet 5 was missing here until 2026-07-29, which meant a picked
        # reasoning level silently did nothing on what is now our primary
        # lane for both apps: no adaptive block, no output_config.effort.
        "claude-sonnet-5",
        # Same omission, found 2026-08-16: the router catalog started
        # offering these in #681 while this list did not, so any turn
        # routed to them dropped the level on the floor. It matters most
        # on Opus 5, which is the one model here that thinks by DEFAULT:
        # with no effort field it runs at the API default of `high` with
        # adaptive thinking on, i.e. the most expensive configuration it
        # has, and we had no way to ask for less. Measured 2026-08-14 at
        # that default: 488s and $1.13 for one workbook.
        "claude-opus-4-8",
        "claude-opus-5",
    )
    if any(prefix in m for prefix in explicit_effort_models):
        return True
    if "mythos" in m:
        return True
    return False


def anthropic_accepts_disabled_thinking(model: str) -> bool:
    """True only where OMITTING the thinking field would leave thinking ON.

    Sonnet 5 (and the Opus 5 family) think by default, so a short-output
    lane has to say `{"type": "disabled"}` explicitly or thinking eats the
    `max_tokens` budget it shares with the reply. Everywhere else omission
    already means no thinking, so we omit rather than send a field the
    older models never needed.

    Deliberately False for Fable/Mythos: an explicit disabled block is a
    400 there (thinking is always on), so the caller must omit instead.
    """
    m = model.lower()
    if "fable" in m or "mythos" in m:
        return False
    return "claude-sonnet-5" in m or "claude-opus-5" in m


def anthropic_accepts_temperature(model: str) -> bool:
    """False where `temperature` is a 400, not a knob.

    Anthropic deprecated the parameter from Opus 4.7 onward: any value at
    all (including one equal to the default) returns
    `400 "temperature is deprecated for this model"`. The older models
    still take it, and our determinism-pinned lanes (report 0.2, template
    extraction 0.2, the Haiku classifiers 0.0) depend on that.

    Probed live against the API 2026-07-30, one call per model:
      rejects  opus-4-7, opus-4-8, opus-5, sonnet-5, fable-5
      accepts  sonnet-4-6, opus-4-6, haiku-4-5

    Deny-list rather than allow-list on purpose: an unrecognized model
    keeps today's behavior and fails loudly if Anthropic extends the
    deprecation, instead of silently dropping the pin that a lane's
    reproducibility promise rests on.
    """
    m = (model or "").lower()
    deprecated = (
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
    )
    return not any(prefix in m for prefix in deprecated)


def anthropic_thinking_block(
    level: str | None,
    model: str | None = None,
    *,
    disabled: bool = False,
) -> dict | None:
    """For effort-path models with a non-default level: returns
    `{"type": "adaptive"}`. Anything else (Haiku, "default", empty):
    returns None — the field is omitted, API default applies.

    `disabled=True` is the GP-side lane setting (config key `thinking`),
    not a user pick: it returns `{"type": "disabled"}` on models that
    need it stated explicitly, and None everywhere else (where omitting
    is already equivalent and safer). It wins over `level` — a lane that
    declares itself thinking-free stays that way regardless of picker.
    """
    if disabled:
        if model and anthropic_accepts_disabled_thinking(model):
            return {"type": "disabled"}
        return None
    if not level or level == "default":
        return None
    if model and anthropic_uses_effort_path(model):
        return {"type": "adaptive"}
    return None


# Anthropic caps the two highest effort levels when thinking is turned
# off explicitly: on Opus 5, `thinking: {type: "disabled"}` is accepted
# at `high` or below and returns a 400 at `xhigh` or `max`. Both halves
# are reachable independently here (a lane declares itself thinking-free
# in config; the level comes from the picker), so the combination has to
# be resolved on our side rather than discovered as a failed turn.
_DISABLED_THINKING_EFFORT_CAP = ("claude-opus-5",)
_CAPPED_EFFORTS = ("xhigh", "max")


def anthropic_output_config(level: str | None, model: str, *,
                            thinking_disabled: bool = False) -> dict | None:
    """Effort-path models: `output_config: {effort: <level>}`. Pass-through.
    "default" → omit (Anthropic API default of `"high"` applies).

    One clamp, not a pass-through: a thinking-free lane asking for
    `xhigh`/`max` on a model that forbids that pairing is lowered to
    `high` rather than sent and rejected. Clamping beats dropping the
    field, which would silently land on the same API default of `high`
    with no record that a level was ever picked.
    """
    if not level or level == "default" or not anthropic_uses_effort_path(model):
        return None
    m = (model or "").lower()
    if (thinking_disabled and level in _CAPPED_EFFORTS
            and any(p in m for p in _DISABLED_THINKING_EFFORT_CAP)):
        return {"effort": "high"}
    return {"effort": level}


def _is_gemini_3(model: str) -> bool:
    m = model.lower()
    return m.startswith("gemini-3") or m.startswith("gemini-3.")


def gemini_thinking_config(level: str | None, model: str) -> dict | None:
    """Gemini 3.x: `thinkingConfig: {thinkingLevel: <value>}` — pass-through.
    "default" → omit (Gemini's dynamic-high default applies).
    Gemini 2.5.x (none in current config) uses integer thinkingBudget —
    returns None for that family."""
    if not level or level == "default":
        return None
    if _is_gemini_3(model):
        return {"thinkingLevel": level}
    return None
