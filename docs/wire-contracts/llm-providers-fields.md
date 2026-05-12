# `llm-providers.json` — per-model capability fields

Status:
- **PR B (2026-05-12)** added 7 per-model sampling/IO fields.
- **PR A1 (2026-05-12)** added `reasoningLevels` + `promptReserveTokens` per model and a top-level `defaultPromptReserveTokens` fallback (Option A consolidation — `llm-providers.json` is now the canonical iOS-facing config).

Owner (server): GP. Owner (client): SS iOS.

## Why these fields exist

SS audited their consumption of `llm-providers.json` and `model-capabilities.json` and asked GP to expose a handful of per-model facts that iOS was previously guessing or hardcoding. Boss decision: put them in `llm-providers.json` next to the other per-model identity/capability fields (rather than splitting them into `model-capabilities.json` which is now primarily the reasoning-picker driver).

## Per-model fields

Each entry under `providers[*].models[*]` carries:

| Field | Type | Added | Meaning |
|---|---|---|---|
| `maxOutputTokens` | `int \| null` | PR B | Provider's documented max output tokens for this model. `null` means GP picks at runtime (only `cloudzap.auto`). |
| `temperatureDefault` | `float \| null` | PR B | Recommended sampling temperature. `null` = **iOS must OMIT the field on the wire** (e.g. Anthropic adaptive-thinking models 400 when temperature is set). |
| `maxImagesPerRequest` | `int \| null` | PR B | Hard cap on image attachments per request. `0` = model is text-only even though it might be on a vision-capable provider. `null` = managed at runtime. |
| `streamingSupported` | `bool` | PR B | True if SSE / chunked streaming works end-to-end for this model. |
| `toolUseSupported` | `bool` | PR B | True if function/tool calling works. Future agentic features will gate on this. |
| `cacheControlSupported` | `bool` | PR B | Anthropic-only prompt caching. iOS may splice `cache_control` markers into the body when this is true. Set on Anthropic native + `anthropic/*` OR routes + `cloudzap.auto` (GP handles cache_control server-side when routing). |
| `serverManaged` | `bool` | PR B | True only for `cloudzap.auto`. Signals "GP picks the underlying model at runtime; ignore the other per-model fields here, the chosen model's fields will apply." |
| `reasoningLevels` | `array<string> \| null` | PR A1 | Provider-native reasoning vocabulary the model accepts (e.g. Anthropic Opus 4.7 = `["default","low","medium","high","xhigh","max"]`; Kimi K2.5 = `["default","disabled","enabled"]`). `null` for models with no string-vocabulary picker. Picker shows iff `supportsReasoning && reasoningLevels` is non-empty. |
| `promptReserveTokens` | `int \| null` | PR A1 | Per-model override for the prompt-reserve budget iOS subtracts from the context window when sizing chat history. `null` means use the file-level fallback (`defaultPromptReserveTokens`). |

## Top-level fields

| Field | Type | Added | Meaning |
|---|---|---|---|
| `version` | `int` | (always present) | Monotonic config version. iOS sends `X-Config-Version: N` and gets fresh payload when `N < server version`. |
| `defaultPromptReserveTokens` | `int` | PR A1 | File-level fallback for `promptReserveTokens` when a model's per-model value is `null`. Today: `8000`. |
| `providers` | `array` | (always present) | The provider list. |

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

## Relationship to `model-capabilities.json`

PR A1 (Option A) consolidated all iOS-facing per-model fields into `llm-providers.json`. The remaining `model-capabilities.json` carries server-side routing intelligence (`contextSlots`, `contextQuilt`, `splitModelSummary`, `estimatedAvailableTokens`) that has no business being shipped over the wire to clients.

PR A3 will:
- Move that server-side routing intelligence into `config/internal/model-routing.json` (not published via `/v1/config/*`).
- Remove the `/v1/config/model-capabilities` endpoint entirely once SS's iOS PR A2 has shipped + clients have rolled forward.

Until then, the existing `model-capabilities.json` keeps being published so older iOS builds continue to work, but new iOS code (post-A2) reads everything from `llm-providers.json`.

## Versioning

`llm-providers.json` version history:
- v9 → v10 (PR B, 2026-05-12): added the 7 sampling/IO fields.
- v10 → v11 (PR A1, 2026-05-12): added `reasoningLevels`, `promptReserveTokens` per model and `defaultPromptReserveTokens` top-level.

iOS sends `X-Config-Version: N` and gets fresh payload when `N < server version`. Builds that don't know the newer fields ignore them gracefully (forward-compatible).

## Failure modes

- If iOS reads `temperatureDefault: null` and still sends a temperature field, the provider 4xx is the right surface. GP doesn't proxy these BYOK calls; the error lands client-side.
- If a new model is added without these fields, the schema test `tests/test_llm_providers_per_model_fields.py` fails CI — values are required, not optional.
