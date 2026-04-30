# Reasoning level — wire contract

Status: **shipped server-side, awaiting iOS adoption**.
Owner (server): GP. Owner (client): SS iOS.

## What changed on the wire

`/v1/chat` accepts a new optional field on the request body:

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "system_prompt": "...",
  "user_content": "...",
  "reasoning": "off" | "low" | "medium" | "high"
}
```

When `reasoning` is omitted (or `null`), the provider's own default is
used — existing iOS builds keep working with no change.

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

A single 4-way picker tied to the model selector. Suggested copy:

- **Off** — fastest, cheapest. Use for short factual queries.
- **Low** — light reasoning. Default for mid-meeting.
- **Medium** — balanced.
- **High** — deepest reasoning. Use for post-meeting analysis.

Hide the picker when the selected model has no reasoning support
(today: Apple on-device, Perplexity Sonar, Llama 4 Maverick, Mistral
Large, Gemini 1.x). For the rest of the catalog the picker is safe.

The picker should default to `null` (don't send the field) on first
launch so we inherit per-model defaults rather than forcing a level.
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
