# `llm-providers.json` — per-model capability fields

Status: **PR B (2026-05-12) added 7 per-model capability fields.**
Owner (server): GP. Owner (client): SS iOS.

## Why these fields exist

SS audited their consumption of `llm-providers.json` and `model-capabilities.json` and asked GP to expose a handful of per-model facts that iOS was previously guessing or hardcoding. Boss decision: put them in `llm-providers.json` next to the other per-model identity/capability fields (rather than splitting them into `model-capabilities.json` which is now primarily the reasoning-picker driver).

## The 7 fields

Each entry under `providers[*].models[*]` carries:

| Field | Type | Meaning |
|---|---|---|
| `maxOutputTokens` | `int \| null` | Provider's documented max output tokens for this model. `null` means GP picks at runtime (only `cloudzap.auto`). |
| `temperatureDefault` | `float \| null` | Recommended sampling temperature. `null` = **iOS must OMIT the field on the wire** (e.g. Anthropic adaptive-thinking models 400 when temperature is set). |
| `maxImagesPerRequest` | `int \| null` | Hard cap on image attachments per request. `0` = model is text-only even though it might be on a vision-capable provider. `null` = managed at runtime. |
| `streamingSupported` | `bool` | True if SSE / chunked streaming works end-to-end for this model. |
| `toolUseSupported` | `bool` | True if function/tool calling works. Future agentic features will gate on this. |
| `cacheControlSupported` | `bool` | Anthropic-only prompt caching. iOS may splice `cache_control` markers into the body when this is true. Set on Anthropic native + `anthropic/*` OR routes + `cloudzap.auto` (GP handles cache_control server-side when routing). |
| `serverManaged` | `bool` | True only for `cloudzap.auto`. Signals "GP picks the underlying model at runtime; ignore the other per-model fields here, the chosen model's fields will apply." |

## iOS read semantics

For each model the user selects:

1. **Render the picker UI** using these fields:
   - Use `maxImagesPerRequest` for image-picker cap (don't allow 4 images on a model with `maxImagesPerRequest: 3`).
   - Disable streaming UI affordances when `streamingSupported: false`.
   - Hide tool-use affordances when `toolUseSupported: false`.
2. **Construct the request body**:
   - Set `max_tokens` (or provider equivalent) to `maxOutputTokens` (or user's preference, capped at this value).
   - Set temperature to `temperatureDefault` **unless it is `null`**; if `null`, omit the temperature field entirely from the body.
   - For Anthropic models with `cacheControlSupported: true` and the right context layout, optionally splice `cache_control: {type: "ephemeral"}` markers.
3. **GP-managed routing**: when `serverManaged: true`, iOS doesn't make BYOK direct calls. Hit GP's `/v1/chat` instead and let GP pick provider + model + handle cache_control / budget gate / search caps server-side.

## Per-model value rationale

- **OpenAI gpt-5.x** — `temperatureDefault: 0.3` chosen for SS's meeting-assistant workload (summarization, accurate Q&A); OpenAI's API default of `1.0` is too high for factual output.
- **Anthropic Opus 4.7 / Sonnet 4.6** — `temperatureDefault: null`. These models 400 when `temperature` is sent alongside `thinking: {type: "adaptive"}`. iOS MUST omit. (Documented behavior from live smoke during reasoning vocabulary rev-3.)
- **Anthropic Haiku 4.5** — `temperatureDefault: 0.3`. Legacy manual `budget_tokens` path; Anthropic accepts temperature here.
- **Moonshot Kimi K2.5 / K2.6 / K2-Thinking** — temperatures match Moonshot's [recommended mode-specific defaults](https://platform.moonshot.ai/docs): `0.6` for K2.5 instant mode, `1.0` for K2.6 and K2-thinking which think by default.
- **Gemini 3.x / Grok 4.x / DeepSeek V4 / Qwen 3.x** — `temperatureDefault: 0.3` for the same meeting-assistant rationale as OpenAI.
- **`cloudzap.auto`** — every numeric field is `null` because GP picks the underlying model at runtime; the actual per-model fields will apply to whatever model GP chose. `serverManaged: true` is the signal to iOS that this is the GP-managed path.

## Partition with `model-capabilities.json`

After PR B, the two configs serve clearly distinct purposes:

- **`llm-providers.json`** — identity (provider, model, costs, context window, vision) + **capability constraints** (this PR's 7 fields) + auth/wire details (baseURL, header names, etc.).
- **`model-capabilities.json`** — reasoning-picker driver (`supportsReasoning`, `reasoningLevels`) + memory/token budget UX values (`promptReserveTokens`).

PR A (next) will clean up dead fields in `model-capabilities.json` (SS confirmed ~80% of its schema is unused) and remove the now-redundant `supportsReasoning` from `llm-providers.json`.

## Versioning

`llm-providers.json` bumped 9 → 10 in PR B. iOS sends `X-Config-Version: 9` on next fetch and gets the full v10 payload back. Existing iOS that doesn't know about the new fields ignores them gracefully (forward-compatible).

## Failure modes

- If iOS reads `temperatureDefault: null` and still sends a temperature field, the provider 4xx is the right surface. GP doesn't proxy these BYOK calls; the error lands client-side.
- If a new model is added without these fields, the schema test `tests/test_llm_providers_per_model_fields.py` fails CI — values are required, not optional.
