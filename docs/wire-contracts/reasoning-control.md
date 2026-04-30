# Reasoning level — wire contract

Status: **shipped server-side. Per-model `reasoningLevels` field added 2026-04-30.**
Owner (server): GP. Owner (client): SS iOS.

## What iOS reads now

Each model in `model-capabilities.json` carries a `reasoningLevels` array
(when `supportsReasoning: true`). iOS renders **only** those buttons in
the picker — no "Default" button, no provider-level guesswork.

```json
"claude-haiku-4-5": {
  "supportsReasoning": true,
  "reasoningLevels": ["off", "low", "medium", "high"]
},
"grok-4": {
  "supportsReasoning": true,
  "reasoningLevels": ["low", "high"]
},
"kimi-k2.5": {
  "supportsReasoning": true,
  "reasoningLevels": ["off", "high"]
},
"foundation-models": {
  "supportsReasoning": false
  // no reasoningLevels field; picker hidden
}
```

**iOS rules:**
1. If `supportsReasoning: false` OR `reasoningLevels` is absent/empty, hide the picker entirely.
2. Otherwise show one button per entry in `reasoningLevels`, in array order.
3. Send the chosen value as `reasoning` on `/v1/chat`. Always send an explicit value (never `null`, never `"default"`).
4. Persist user's last choice per (provider, model) pair so model-switches don't surprise them.

## Per-model levels (Day 1)

| Provider family | `reasoningLevels` | Reason |
|---|---|---|
| OpenAI gpt-5.x | `["off", "low", "medium", "high"]` | Native 4-level support |
| Anthropic Haiku/Sonnet/Opus 4.x | `["off", "low", "medium", "high"]` | Continuous `budget_tokens`, mapped to 4 buckets |
| Google Gemini 3.x | `["off", "low", "medium", "high"]` | Continuous `thinkingBudget`, mapped to 4 buckets |
| xAI Grok 4 / 4.1 | `["low", "high"]` | Native 2-level support; no real "off" mode |
| Moonshot Kimi K2.x | `["off", "high"]` | Boolean `enable_thinking` |
| Alibaba Qwen 3.5 | `["off", "high"]` | Boolean `enable_thinking` |
| DeepSeek V4 Flash/Pro | `["off", "high"]` | Server collapses low/medium → high; don't fake granularity |
| Apple Foundation Models | (picker hidden) | No reasoning support |

## /v1/chat request

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "system_prompt": "...",
  "user_content": "...",
  "reasoning": "off" | "low" | "medium" | "high"
}
```

**Validation:** the server's `ReasoningLevel` literal accepts only
`"off"`, `"low"`, `"medium"`, `"high"` (or omit the field). Sending
`"default"` returns a 422 validation error.

When `reasoning` is omitted entirely (legacy clients), the provider's own
default is used — but iOS should always send an explicit value going
forward.

This is a single normalized knob. Per-provider translation lives in
`app/services/providers/reasoning.py`; iOS does not need to learn each
provider's native field name.

## Mapping (for reference / OpenRouter mirror)

| Level | OpenAI gpt-5.x | xAI grok-4 | DeepSeek v4 | Kimi / Qwen | Anthropic | Gemini |
|---|---|---|---|---|---|---|
| `off` | `reasoning_effort: "minimal"` | `reasoning_effort: "low"` | `thinking: {disabled}` | `enable_thinking: false` | (no thinking block) | `thinkingConfig: {thinkingBudget: 0}` |
| `low` | `"low"` | `"low"` | `{enabled}` + `"low"` | `enable_thinking: true` | `budget_tokens: 1024` | `thinkingBudget: 1024` |
| `medium` | `"medium"` | `"high"` (no medium) | `{enabled}` + `"medium"` | `enable_thinking: true` | `budget_tokens: 4096` | `thinkingBudget: 4096` |
| `high` | `"high"` | `"high"` | `{enabled}` + `"high"` | `enable_thinking: true` | `budget_tokens: 16384` | `thinkingBudget: 16384` |

When the Anthropic `thinking` block is set, the adapter automatically
lifts `max_tokens` to `budget_tokens + 1024` so the response has
headroom (Anthropic requires `budget_tokens < max_tokens`).

## OpenRouter (called direct from iOS, not proxied)

iOS hits OpenRouter directly with the user's BYO key — there is no
GP adapter in that path. To keep behavior identical between
`provider: "cloudzap"` and `provider: "openrouter"` in SS, send
OpenRouter's unified reasoning block:

```json
{
  "model": "deepseek/deepseek-v4-pro",
  "messages": [...],
  "reasoning": { "effort": "low" | "medium" | "high" }
}
```

For `reasoning="off"` on OpenRouter, omit the `reasoning` block
entirely (OpenRouter has no explicit "off"; some upstream providers
default to no thinking, others default on — accept that the
"off" guarantee is weaker on OpenRouter than on the GP-managed path).

OpenRouter docs: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

## Suggested iOS UI

Render only the buttons listed in the active model's `reasoningLevels`
array. Suggested copy when the level is exposed:

- **Off** — fastest, cheapest. Use for short factual queries.
- **Low** — light reasoning. Default for mid-meeting.
- **Medium** — balanced.
- **High** — deepest reasoning. Use for post-meeting analysis.

Picker visibility is determined entirely by `model-capabilities.json`:
- `supportsReasoning: false` → picker hidden.
- `supportsReasoning: true` + `reasoningLevels: [...]` → picker shows those buttons in order.

Persist the user's last selection per (provider, model) pair.

## Test plan (iOS side)

1. Picker hidden for models without reasoning support; visible for the rest.
2. Selecting "Off" on a Claude model: response arrives normally,
   no `thinking` content in the stream (no thinking block was sent).
3. Selecting "High" on DeepSeek V4 Pro: noticeably slower first-token
   latency; response quality on a reasoning prompt should improve.
4. Selecting "High" on OpenRouter→Claude Opus 4.7: same observable
   latency increase as direct GP→Claude Opus 4.7.
5. Existing builds (no `reasoning` field): no behavior change.

## When to add a new model with reasoning support

If the new model uses an existing provider's shape, no code changes
needed. Otherwise add a branch in
`app/services/providers/reasoning.py::openai_compat_fields` (or the
matching helper) and update the table above.
