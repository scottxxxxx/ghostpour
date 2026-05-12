# Reasoning level — wire contract

Status: **vocabulary rev 3 (2026-05-11) — per-model native values, no normalization.**
Owner (server): GP. Owner (client): SS iOS.

## Design

Each model in `model-capabilities.json` lists its accepted reasoning values verbatim in `reasoningLevels`. iOS picks one and sends it as `reasoning` on `/v1/chat`. The server adapter slots that value into the right provider-native field. No translation tables, no normalized vocabulary.

The one universal value is **`"default"`**: first entry in every reasoning-enabled model's array; signals "omit the field, use the provider's API default behavior." iOS shows it pre-selected.

## What iOS reads

```json
"gpt-5.5": {
  "supportsReasoning": true,
  "reasoningLevels": ["default", "none", "low", "medium", "high", "xhigh"]
}
```

iOS rules:
1. If `supportsReasoning: false` or `reasoningLevels` is absent/empty → hide picker.
2. Otherwise show one button per array entry in order. First entry (`"default"`) is the pre-selection.
3. Send chosen value as `reasoning: <value>` on `/v1/chat`. The wire `reasoning` field is a free string — provider-native values vary per model.

## Per-model arrays (current state)

| Model | `reasoningLevels` |
|---|---|
| `gpt-5.5` | `["default", "none", "low", "medium", "high", "xhigh"]` |
| `gpt-5.2` | `["default", "none", "low", "medium", "high", "xhigh"]` |
| `gpt-5-mini` | `["default", "minimal", "low", "medium", "high"]` |
| `gpt-5-nano` | `["default", "minimal", "low", "medium", "high"]` |
| `claude-opus-4-7` | `["default", "low", "medium", "high", "xhigh", "max"]` |
| `claude-sonnet-4-6` | `["default", "low", "medium", "high", "max"]` |
| `claude-haiku-4-5` | (picker hidden — manual `budget_tokens: int`, no string vocabulary) |
| `gemini-3-flash-preview` | `["default", "minimal", "low", "medium", "high"]` |
| `gemini-3.1-flash-lite-preview` | `["default", "minimal", "low", "medium", "high"]` |
| `gemini-3.1-pro-preview` | `["default", "low", "medium", "high"]` |
| `grok-4`, `grok-4.1-fast` | `["default", "none", "low", "medium", "high"]` |
| `kimi-k2.5`, `kimi-k2.6` | `["default", "disabled", "enabled"]` |
| `kimi-k2-thinking` | (picker hidden — always thinks, no toggle) |
| `deepseek-v4-flash`, `deepseek-v4-pro` | `["default", "disabled", "enabled"]` |
| `qwen3-max`, `qwen-plus`, `qwen-flash` | (picker hidden — `enable_thinking` is bool, no string vocabulary) |
| `foundation-models` | (picker hidden — Apple, no reasoning) |

## Server-side translation

The adapter's only job is to slot the value into the right field. `"default"` (or null/empty) means omit.

| Provider | Wire shape when `reasoning: <value>` is non-`"default"` |
|---|---|
| OpenAI gpt-5.x | `reasoning_effort: <value>` |
| xAI Grok | `reasoning_effort: <value>` |
| Anthropic Opus 4.7 / Opus 4.6 / Sonnet 4.6 / Mythos | `thinking: {type: "adaptive"}` + `output_config: {effort: <value>}` |
| Google Gemini 3.x | `thinkingConfig: {thinkingLevel: <value>}` |
| Moonshot Kimi K2.5 / K2.6 | `thinking: {type: <value>}` |
| DeepSeek V4 | `thinking: {type: <value>}` |
| Qwen 3.x | (picker hidden — bool field, not a string vocabulary) |

When `reasoning: "default"` (or omitted): GP sends no reasoning-related fields. Each provider's API default applies (e.g., gpt-5.5 → medium thinking; Opus 4.7 → high effort; Kimi K2.6 → thinking on by default; Kimi K2.5 → Instant mode default).

## OpenRouter (iOS → OR direct, GP not in path)

OR uses its own unified `reasoning` block. iOS maps the picker value:

| iOS picker value | OpenRouter request body |
|---|---|
| `"default"` | Omit `reasoning` block entirely |
| `"none"` / `"disabled"` | `reasoning: { effort: "none" }` (OR supports `none` as explicit disable) |
| `"minimal"` / `"low"` / `"medium"` / `"high"` / `"xhigh"` | `reasoning: { effort: <value> }` |
| `"max"` (Anthropic-only) | Pass `effort: "max"` through — OR forwards to Anthropic |
| `"enabled"` | OR doesn't take literal `"enabled"`; iOS should map → `effort: "high"` (sensible default for "yes, think") |

## Hidden-picker rationale

A model is hidden from the reasoning picker (`supportsReasoning: false`) when:
- The provider field accepts a non-string type (integer like Gemini 2.5 `thinkingBudget`, Anthropic Haiku `budget_tokens`; boolean like Qwen `enable_thinking`)
- The model has no toggle at all (Kimi K2-Thinking always thinks)

In these cases iOS doesn't render the reasoning section. The model still works; users just don't get reasoning-level controls.
