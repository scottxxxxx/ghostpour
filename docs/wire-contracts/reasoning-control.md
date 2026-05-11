# Reasoning level — wire contract

Status: **shipped server-side. Vocabulary rev 2 (2026-05-11): `default | minimal | low | medium | high`.**
Owner (server): GP. Owner (client): SS iOS.

## What iOS reads now

Each model in `model-capabilities.json` carries a `reasoningLevels` array
(when `supportsReasoning: true`). iOS renders **only** those buttons in
the picker — no "Default" button when the array doesn't include `"default"`,
no provider-level guesswork.

```json
"claude-haiku-4-5": {
  "supportsReasoning": true,
  "reasoningLevels": ["default", "low", "medium", "high"]
},
"gpt-5.5": {
  "supportsReasoning": true,
  "reasoningLevels": ["default", "minimal", "low", "medium", "high"]
},
"grok-4": {
  "supportsReasoning": true,
  "reasoningLevels": ["low", "high"]
},
"foundation-models": {
  "supportsReasoning": false
  // no reasoningLevels field; picker hidden
}
```

**iOS rules:**
1. If `supportsReasoning: false` OR `reasoningLevels` is absent/empty, hide the picker entirely.
2. Otherwise show one button per entry in `reasoningLevels`, in array order.
3. Send the chosen value as `reasoning` on `/v1/chat`. Always send an explicit value (never `null`).
4. Persist user's last choice per (provider, model) pair so model-switches don't surprise them.

## Vocabulary

| Value | Meaning |
|---|---|
| `default` | Let the provider decide. Translates to "omit the reasoning field" on most providers, or "force-disable thinking" on binary-toggle providers (Kimi/Qwen/DeepSeek) where omission would let the provider think anyway. The cheapest path on every provider where the cheapest is well-defined. |
| `minimal` | The lowest non-default native level. Only meaningful on providers that have it natively: **OpenAI gpt-5.x** and **Gemini 3 Flash / Flash-Lite**. Hidden in the picker for every other model. |
| `low` / `medium` / `high` | Explicit per-provider thinking levels. Each mapped to that provider's native budget / effort value. |

## Per-model levels (current)

| Provider family | Models | `reasoningLevels` |
|---|---|---|
| OpenAI gpt-5.x | `gpt-5.5`, `gpt-5.5-pro`, `gpt-5.2`, `gpt-5-mini`, `gpt-5-nano` | `[default, minimal, low, medium, high]` |
| Anthropic Claude 4.x | `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5` | `[default, low, medium, high]` |
| Google Gemini 3 Flash / Flash-Lite | `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview` | `[default, minimal, low, medium, high]` |
| Google Gemini 3 Pro | `gemini-3.1-pro-preview` | `[default, low, medium, high]` (no `minimal` per Google) |
| xAI Grok 4 / 4.1 | `grok-4`, `grok-4.1-fast` | `[low, high]` (no `default` — Grok's omit-default is observationally `low`) |
| Moonshot Kimi K2.x | `kimi-k2.5`, `kimi-k2-thinking`, `kimi-k2-turbo-preview` | `[default, high]` (boolean toggle; `default` force-disables) |
| Alibaba Qwen 3.x | `qwen-max`, `qwen-plus`, `qwen-flash` | `[default, high]` (boolean toggle; `default` force-disables) |
| DeepSeek V4 | `deepseek-v4-flash`, `deepseek-v4-pro` | `[default, high]` (server collapses low/medium → high) |
| Apple Foundation Models | `foundation-models` | (picker hidden — `supportsReasoning: false`) |

## /v1/chat request

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "system_prompt": "...",
  "user_content": "...",
  "reasoning": "default" | "minimal" | "low" | "medium" | "high"
}
```

**Validation:** the server's `ReasoningLevel` literal accepts only
`"default"`, `"minimal"`, `"low"`, `"medium"`, `"high"` (or omit the field).
Sending any other value (including legacy `"off"`) returns a 422.

When `reasoning` is omitted entirely, the helper still resolves a sensible
shape per provider (same as `default`).

This is a single normalized knob. Per-provider translation lives in
`app/services/providers/reasoning.py`; iOS does not need to learn each
provider's native field name.

## Mapping (server-side translation)

| Level | OpenAI gpt-5.x | Anthropic | Gemini 3 (Flash / Flash-Lite) | Gemini 3 Pro | xAI Grok | Kimi / Qwen | DeepSeek V4 |
|---|---|---|---|---|---|---|---|
| `default` | (omit) | (no thinking block) | (omit `thinkingConfig`) | (omit `thinkingConfig`) | (hidden in picker) | `enable_thinking: false` | `thinking: {disabled}` |
| `minimal` | `reasoning_effort: "minimal"` | (hidden) | `thinkingLevel: "minimal"` | (hidden; defensively → `low`) | (hidden; defensively → `low`) | (hidden; defensively → `enable_thinking: false`) | (hidden; defensively → `thinking: {disabled}`) |
| `low` | `"low"` | `budget_tokens: 1024` | `thinkingLevel: "low"` | `thinkingLevel: "low"` | `reasoning_effort: "low"` | (hidden) | (hidden) |
| `medium` | `"medium"` | `budget_tokens: 4096` | `thinkingLevel: "medium"` | `thinkingLevel: "medium"` | (hidden; collapse → `high`) | (hidden) | (hidden) |
| `high` | `"high"` | `budget_tokens: 16384` | `thinkingLevel: "high"` | `thinkingLevel: "high"` | `reasoning_effort: "high"` | `enable_thinking: true` | `thinking: {enabled}, reasoning_effort: "high"` |

When the Anthropic `thinking` block is set, the adapter automatically
lifts `max_tokens` to `budget_tokens + 1024` so the response has
headroom (Anthropic requires `budget_tokens < max_tokens`).

For Gemini 2.5.x (no models in current `model-capabilities.json` but
adapter dispatches by model family): `default` → omit;
`low`/`medium`/`high` → `thinkingBudget: 1024 / 4096 / 16384`;
`minimal` → `thinkingBudget: 0` (Flash/Flash-Lite only — Pro doesn't accept 0).

## OpenRouter (called direct from iOS, not proxied)

iOS hits OpenRouter directly with the user's BYO key — there is no
GP adapter in that path. To keep behavior identical between
`provider: "cloudzap"` and `provider: "openrouter"` in SS, send
OpenRouter's unified reasoning block:

```jsonc
// OpenRouter request body
{
  "model": "google/gemini-3-flash-preview",
  "messages": [...],
  "reasoning": { "effort": "minimal" | "low" | "medium" | "high" }
}
```

**iOS → OpenRouter mapping:**

| iOS picker value | OpenRouter request shape |
|---|---|
| `default` | **Omit the `reasoning` block entirely.** OpenRouter falls back to the upstream provider's default. |
| `minimal` | `reasoning: { effort: "minimal" }` (OpenRouter supports this for OpenAI gpt-5 and Gemini 3 Flash/Flash-Lite) |
| `low` | `reasoning: { effort: "low" }` |
| `medium` | `reasoning: { effort: "medium" }` |
| `high` | `reasoning: { effort: "high" }` |

If iOS needs to force-disable thinking through OpenRouter on a model
where the provider's default *is* thinking-on (Gemini 3 in particular —
default is dynamic `high`), use `reasoning: { max_tokens: 0 }`. OpenRouter
translates this to `thinkingBudget: 0` for Gemini. Today this isn't on
the picker; if a user-facing "force off on OR" button is wanted,
introduce a separate `reasoningLevels` array variant for OpenRouter-routed
models.

OpenRouter docs: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

## Suggested iOS UI

Render only the buttons listed in the active model's `reasoningLevels`
array. Suggested copy when each level is exposed:

- **Default** — let the model decide. Cheapest / fastest path on most providers.
- **Minimal** — request the smallest non-default thinking budget. *(Only OpenAI gpt-5.x and Gemini 3 Flash / Flash-Lite.)*
- **Low** — light reasoning.
- **Medium** — balanced.
- **High** — deepest reasoning. Use for post-meeting analysis.

Picker visibility is determined entirely by `model-capabilities.json`:
- `supportsReasoning: false` → picker hidden.
- `supportsReasoning: true` + `reasoningLevels: [...]` → picker shows those buttons in order.

Persist the user's last selection per (provider, model) pair.

## Test plan (iOS side)

1. Picker hidden for models without reasoning support; visible for the rest.
2. **`gpt-5.5` shows 5 buttons including "Minimal"**; Anthropic Haiku shows 4 (no Minimal).
3. **Gemini 3 Flash shows 5 buttons including "Minimal"**; Gemini 3 Pro shows 4 (no Minimal).
4. **Grok 4 shows only Low/High** (no Default).
5. **Kimi/Qwen/DeepSeek show only Default/High** (binary toggle).
6. Selecting "Default" on Claude: response arrives with no `thinking` content (no thinking block sent).
7. Selecting "Minimal" on Gemini 3 Flash via GP: provider response confirms `thinkingLevel: "minimal"` was applied (verify via thinking-token usage in `usageMetadata.thoughtsTokenCount`).
8. Selecting each level on OpenRouter→Gemini 3 Flash: latency / `usage.completion_tokens_details.reasoning_tokens` increases with level.

## When to add a new model with reasoning support

If the new model uses an existing provider's shape, just add an entry in
`model-capabilities.json` with the right `reasoningLevels` array. Otherwise
add a branch in `app/services/providers/reasoning.py::openai_compat_fields`
(or the matching helper) and update the per-model table above.
