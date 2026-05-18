# Reasoning vocabulary — Rev 3

**Status:** Spec draft (2026-05-18). Supersedes [reasoning-control.md](reasoning-control.md).
**Owner (server):** GP. **Owner (client):** SS iOS.
**Related handoffs:** [`docs/handoffs/ss-reasoning-rev2.md`](../handoffs/ss-reasoning-rev2.md) (prior rev), [`docs/handoffs/ss-byok-token-limit-field-reply.md`](../handoffs/ss-byok-token-limit-field-reply.md) (Rev 3 conversation thread).

## What changed from Rev 2

Rev 2 (2026-05-11): iOS picker rendered every reasoning-enabled model's `reasoningLevels` array as effort-style buttons. Single semantic family.

Rev 3 (this spec) adds:
1. A new per-model field — `reasoningFamily: "effort" | "toggle"` — that classifies the model's reasoning semantic.
2. New tokens in the **effort** family: `none`, `xhigh`, `max`.
3. New tokens in the **toggle** family: `disabled`, `enabled`. (The toggle family is new; under Rev 2 these tokens already existed in our config but iOS silently dropped them because they aren't valid effort levels.)
4. A forward-compat rule: when `reasoningFamily` is absent or carries an unknown value, iOS treats the model as `effort` (Rev 2 behavior).
5. The `default` semantic is explicitly uniform across families: omit the override on the wire; the provider's API default applies.

No wire-shape changes for the request body. The provider-native translation table from Rev 2 is unchanged — only the iOS UI dispatch dimension is new.

## Vocabulary

### Effort family

Effort levels are an ordered scale. Picker renders one button per level, in the order they appear in the model's `reasoningLevels` array. The first entry is always `default` and is pre-selected.

| Token | Meaning | UI label (en) | New in Rev 3 |
|---|---|---|---|
| `default` | Use the provider's API default | "Default" | no |
| `none` | Force reasoning off (provider-native disable) | "None" | yes |
| `minimal` | Smallest non-zero reasoning effort | "Minimal" | no |
| `low` | Low effort | "Low" | no |
| `medium` | Medium effort | "Medium" | no |
| `high` | High effort | "High" | no |
| `xhigh` | Extra-high effort (beyond `high`) | "Extra High" | yes |
| `max` | Anthropic-specific maximum thinking budget | "Max" | yes |

Not every model accepts every token. Models advertise the subset they accept via `reasoningLevels`. iOS sends the chosen token verbatim on `/v1/chat` as `reasoning: <value>`.

### Toggle family

Toggle is a three-state choice — boolean reasoning toggle plus the universal `default`. Picker renders as a compact three-position switch, not as effort buttons.

| Token | Meaning | UI label (en) | New in Rev 3 |
|---|---|---|---|
| `default` | Use the provider's API default | "Default" | no |
| `disabled` | Explicitly turn reasoning off | "Off" | yes (as family member) |
| `enabled` | Explicitly turn reasoning on | "On" | yes (as family member) |

Toggle-family models advertise `reasoningLevels: ["default", "disabled", "enabled"]`. iOS recognizes this as a toggle and renders the switch UI.

### Localization commitments

| Token | en | es | ja |
|---|---|---|---|
| `default` | Default | Por defecto | デフォルト |
| `none` | None | Ninguno | なし |
| `minimal` | Minimal | Mínimo | 最小 |
| `low` | Low | Bajo | 低 |
| `medium` | Medium | Medio | 中 |
| `high` | High | Alto | 高 |
| `xhigh` | Extra High | Muy alto | 最高 |
| `max` | Max | Máximo | 最大 |
| `disabled` | Off | Desactivado | オフ |
| `enabled` | On | Activado | オン |

Localization strings ship in iOS resources; GP serves only the canonical tokens.

## The `reasoningFamily` field

New per-model field in `llm-providers.json` (and `model-capabilities.json` if/when split):

```json
{
  "id": "kimi-k2.5",
  "supportsReasoning": true,
  "reasoningLevels": ["default", "disabled", "enabled"],
  "reasoningFamily": "toggle"
}
```

Type: `string` enum, currently `"effort" | "toggle"`. Optional.

### Forward-compat rule

When iOS reads a model and finds:

| State of `reasoningFamily` | iOS behavior |
|---|---|
| `"effort"` | Effort-button picker (Rev 2 behavior) |
| `"toggle"` | Three-state switch |
| Absent | Effort-button picker (Rev 2 fallback) |
| Unknown value (e.g., `"continuous"`) | Effort-button picker (Rev 2 fallback) |

This means GP can roll out `reasoningFamily` per model without atomicity constraints. A mid-rollout model without the field renders as it does today — exactly as if Rev 3 hadn't shipped for that entry. Once iOS Rev 3 is live, adding `reasoningFamily: "toggle"` to a model switches its UI to the toggle component on the next config fetch.

### `default` semantic

Across both families, `default` means: **iOS sends `reasoning: "default"` on the wire; GP omits the reasoning-related fields entirely; the provider's API default applies.**

This is the only token whose meaning is part of the family contract (not the family-specific value list). If a future family is introduced where `default` would mean something different, that family must be version-bumped (Rev 4) and the divergence documented.

## UI dispatch by family

### Effort family

Renders as a horizontal row of buttons. Each entry in `reasoningLevels` is a button. The first entry (`default`) is the pre-selected state. Buttons are labeled per the localization table above. Tapping a button selects that level; the chosen value is sent as `reasoning: <value>` on the next `/v1/chat` call.

### Toggle family

Renders as a compact three-state switch with positions `[Default | Off | On]`, in that left-to-right order. Mapping:

| Position | Token sent | Wire effect |
|---|---|---|
| Default | `default` | Reasoning fields omitted; provider default applies |
| Off | `disabled` | Reasoning explicitly disabled |
| On | `enabled` | Reasoning explicitly enabled |

The toggle UI is intentionally smaller than the effort row because there are only three positions and the semantic is binary-with-default. Implementation discretion to SS; we don't prescribe component choice as long as the three states are reachable and the mapping above holds.

## Per-provider wire translation (unchanged from Rev 2)

Restated here for completeness; no change in this rev.

| Provider | Field GP sends when `reasoning: <value>` is non-`default` |
|---|---|
| OpenAI gpt-5.x | `reasoning_effort: <value>` |
| xAI Grok | `reasoning_effort: <value>` |
| Anthropic Opus 4.6/4.7, Sonnet 4.6, Mythos | `thinking: {type: "adaptive"}` + `output_config: {effort: <value>}` |
| Google Gemini 3.x | `thinkingConfig: {thinkingLevel: <value>}` |
| Moonshot Kimi K2.5 / K2.6 | `thinking: {type: <value>}` |
| DeepSeek V4 | `thinking: {type: <value>}` |
| Anthropic Haiku 4.5 | Picker hidden — legacy `budget_tokens: int`, no string vocabulary |
| Qwen 3.x | Picker hidden — `enable_thinking` is bool, no string vocabulary |
| Apple Foundation Models | Picker hidden — no reasoning |

When `reasoning: "default"`: GP sends no reasoning-related fields.

When a model's picker is hidden (per the "Hidden-picker rationale" in [reasoning-control.md](reasoning-control.md)), `reasoningFamily` is absent and iOS doesn't render any UI — irrespective of the family forward-compat rule.

## Per-model family assignment

To be added to `config/remote/llm-providers.json` (and locale mirrors `.es.json`, `tr-llm-providers.json`):

| Model | `reasoningFamily` |
|---|---|
| `gpt-5.5`, `gpt-5.2`, `gpt-5-mini`, `gpt-5-nano` | `effort` |
| `claude-opus-4-7`, `claude-sonnet-4-6` | `effort` |
| `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`, `gemini-3.1-pro-preview` | `effort` |
| `grok-4`, `grok-4.1-fast` | `effort` |
| `kimi-k2.5`, `kimi-k2.6` | `toggle` |
| `deepseek-v4-flash`, `deepseek-v4-pro` | `toggle` |
| OpenRouter mirrors of the above | match the underlying model's family |
| `claude-haiku-4-5-*` | (picker hidden — field absent) |
| `qwen3-max`, `qwen-plus`, `qwen-flash` | (picker hidden — field absent) |
| `foundation-models` | (picker hidden — field absent) |
| `kimi-k2-thinking` (if re-added later) | (picker hidden — always thinks) |

OpenRouter mirrors carry the same `reasoningFamily` as their backing model since OpenRouter's `reasoning: { effort: <value> }` block is OR-mediated and doesn't change the semantic family.

## OpenRouter mapping (unchanged from Rev 2)

iOS-direct OpenRouter requests use OR's unified `reasoning` block. Mapping below; the new `enabled`/`disabled` tokens are OR's effort field with `none` for disable, `high` as a sensible "yes, think" for enable.

| iOS picker value | OpenRouter request body |
|---|---|
| `default` | Omit `reasoning` block entirely |
| `none` / `disabled` | `reasoning: { effort: "none" }` |
| `minimal` / `low` / `medium` / `high` / `xhigh` | `reasoning: { effort: <value> }` |
| `max` (Anthropic-only) | Pass `effort: "max"` through (OR forwards to Anthropic) |
| `enabled` | `reasoning: { effort: "high" }` |

## Migration plan

The sequence is loosely ordered because the forward-compat rule decouples it. Either side can move first.

### Step 1 — Spec ratification (this doc)

GP shares this draft; SS confirms or counters. Open questions list at the end.

### Step 2 — GP config writes (`reasoningFamily` per model)

Add `reasoningFamily` to every reasoning-enabled model in:
- `config/remote/llm-providers.json` (en)
- `config/remote/llm-providers.es.json`
- `config/remote/tr-llm-providers.json`

Bump `version`. Locked in lockstep — same field set across all three locale variants. The `tests/test_llm_providers_per_model_fields.py::test_locales_agree_on_capability_fields` test enforces the model SET; we should additionally extend the parity assertion to require `reasoningFamily` field-level equality before this lands. Tracking that as a parallel cleanup (see "Open questions").

Bundled bump → `sync-from-bundle` → live, per the standard config rollout pattern (see [config-bundle-overlay-sync](../../.claude/projects/-Users-scottguida-cloudzap/memory/reference_config_bundle_overlay_sync.md) memory). Issue #186 tracks the auto-sync proposal that would eliminate this step.

### Step 3 — iOS Rev 3 ship

SS extends `ReasoningLevel` enum to include `none`, `xhigh`, `max`, `disabled`, `enabled`. Adds `reasoningFamily: String?` to `LLMProviderModel` decoder. Implements toggle UI component. Updates picker dispatcher to: read family → effort-row OR toggle-switch.

Forward-compat: if `reasoningFamily` is absent or unknown, fall back to effort-button rendering. This means iOS Rev 3 ships safely even if some model entries lag on the config bump.

### Step 4 — Verification

For each provider, exercise the picker on a real meeting and confirm:
- The right UI renders (effort row for OpenAI/Anthropic/Grok/Gemini; toggle for Kimi/DeepSeek)
- Each picker value produces the correct provider-native wire shape (verified via GP-side request logging or capture)
- `default` everywhere produces a request body with no reasoning fields

The localized `tiers.{locale}.json` strings are not directly involved (this isn't a tier display) — iOS owns the localization for the picker labels.

## Open questions

1. **Strict parity test for `reasoningFamily`.** The current `test_locales_agree_on_capability_fields` only checks the model SET, not per-field equality. The Kimi PR (#188) surfaced that `tr-llm-providers.json` had drifted on version and per-model fields for two prior PRs without CI catching it. Before adding `reasoningFamily` we should tighten the parity test to require field-level agreement on capability-shaping fields (`reasoningLevels`, `reasoningFamily`, `supportsReasoning`, `supportsVision`, `tokenLimitField`, etc.). Otherwise this rev can drift across locales the same way.

2. **`reasoningFamily` on hidden-picker models.** Today we just omit the field for picker-hidden models. iOS only consults `reasoningFamily` after checking `supportsReasoning` and `reasoningLevels`, so omission is safe. Worth confirming SS reads in that order — if not, we should explicitly serve `reasoningFamily: null` for clarity.

3. **Future families.** This rev defines `effort` and `toggle`. Plausible future families if providers diverge further: `budget` (integer-token budget, like Anthropic Haiku today — currently picker-hidden but could become picker-visible if the integer ranges stabilize). Not in scope for Rev 3.

4. **Qwen `enable_thinking` boolean.** Qwen's reasoning toggle is currently picker-hidden because the wire field is boolean, not a string vocabulary. Under Rev 3 it would be a natural fit for the `toggle` family — `enabled` → `enable_thinking: true`, `disabled` → `enable_thinking: false`. Worth doing in a follow-up so Qwen users gain the toggle UI. Out of scope for the initial Rev 3 ship.

5. **Qwen keyURL fix.** Unrelated config cleanup riding along with the Rev 3 config bump per SS's request (current keyURL points to Alibaba's model market instead of API key page).
