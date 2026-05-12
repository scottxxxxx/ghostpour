# SS handoff — per-model capability fields in `llm-providers.json`

Server-side PR #183 landed on 2026-05-12. `llm-providers.json` is at version **10**. Old iOS that doesn't know the new fields ignores them gracefully (forward-compatible) — no app rev forced.

For the authoritative spec see `docs/wire-contracts/llm-providers-fields.md`.

## What's new

7 new per-model fields landed directly on every model entry in `llm-providers.json` (and the `.es` / `tr-` locale variants — capability values are identical across locales).

| Field | Type | Use |
|---|---|---|
| `maxOutputTokens` | `int \| null` | Hard cap on `max_tokens` (or provider equivalent) for this model. `null` means GP picks at runtime — only on `cloudzap.auto`. |
| `temperatureDefault` | `float \| null` | Recommended sampling temperature. **`null` means: do NOT send a temperature field on the wire.** |
| `maxImagesPerRequest` | `int \| null` | Hard cap on image attachments. `0` = text-only. |
| `streamingSupported` | `bool` | Streaming works end-to-end on this model. |
| `toolUseSupported` | `bool` | Function/tool calling works. Future agentic features will gate on this. |
| `cacheControlSupported` | `bool` | Anthropic prompt-caching marker is honored. Currently true on Anthropic native + `anthropic/*` OR routes + `cloudzap.auto` (GP handles cache_control server-side). |
| `serverManaged` | `bool` | True only for `cloudzap.auto`. Tells iOS "this is GP-managed; don't try to build a BYOK body, hit `/v1/chat` and we'll route." |

## What iOS needs to do

### 1. Stop hardcoding

Anywhere iOS was guessing per-model defaults (or hardcoding `0.7` or `4096`), read the field from `llm-providers.json` instead.

### 2. The `temperatureDefault: null` rule

Anthropic Opus 4.7 and Sonnet 4.6 are on the **effort path** (adaptive thinking). Their API rejects `temperature` with a 400 when `thinking: {type: "adaptive"}` is set. We confirmed this with live smoke during the reasoning rev-3 work.

So we set `temperatureDefault: null` on those models and the convention is:

```
if model.temperatureDefault != nil:
    body["temperature"] = model.temperatureDefault
else:
    # omit temperature entirely from the request body
```

If iOS sends `temperature: 0.3` to `claude-opus-4-7` with reasoning enabled, Anthropic returns:
> `temperature: When thinking is set to adaptive, temperature, top_p, and top_k are not allowed.`

The fix is to omit, not to drop-on-error.

### 3. Image cap respects `maxImagesPerRequest`

If a user attaches more than `maxImagesPerRequest` images, the picker UI should prevent it (or warn). Today the limits per provider are mostly conservative (Anthropic = 5, OpenAI = 10, Gemini = 16, Grok = 4, Kimi/Qwen = 5).

`maxImagesPerRequest: 0` means the model is text-only — hide image attachment affordances entirely.

### 4. `serverManaged: true` → use `/v1/chat`

Only `cloudzap.auto` has this set. When the user picks "Auto", iOS sends to GP's `/v1/chat` — GP picks the underlying model, handles cache_control, budget gates, and search caps server-side. No BYOK construction needed for this model.

For every other model (`serverManaged: false`), iOS can BYOK-route directly to the provider using the existing per-provider fields (`baseURL`, `authHeaderName`, `authHeaderPrefix`, `extraHeaders`).

### 5. `cacheControlSupported: true` is an Anthropic-only signal

If you're constructing Anthropic bodies directly and want to opt into prompt caching, you can splice `cache_control: {type: "ephemeral"}` markers into your `system` blocks. The field tells you when that's safe to do. (For GP-routed requests, we splice cache_control server-side, so iOS doesn't need to do it.)

## Per-model temperature choices — why what's there is there

| Model | `temperatureDefault` | Why |
|---|---|---|
| All gpt-5.x | `0.3` | Meeting-assistant workload (factual summaries, accurate Q&A). OpenAI's 1.0 default is too creative. |
| Opus 4.7 / Sonnet 4.6 | `null` | Anthropic 400s when temperature is set with adaptive thinking. iOS must omit. |
| Haiku 4.5 | `0.3` | Manual budget_tokens path accepts temperature. |
| Gemini 3.x, Grok 4.x, DeepSeek V4, Qwen 3.x | `0.3` | Same summarization rationale as OpenAI. |
| Kimi K2.5 | `0.6` | Moonshot's recommended instant-mode default. |
| Kimi K2.6 / K2-Thinking | `1.0` | Moonshot's recommended thinking-mode default. |
| Foundation Models | `0.7` | Apple's default. |
| `cloudzap.auto` | `null` | GP picks the model. |

If these numbers don't match what iOS wants to render in its picker, **say so** — we can tune them. They're starting points based on the meeting-assistant use case; the field is per-model so we can tune individually.

## Versioning + caching

Same pattern as `tiers`, `protected-prompts`, `model-capabilities`:

1. iOS sends `X-Config-Version: 9` on next fetch
2. GP returns full v10 payload (since 9 < 10)
3. iOS caches and bumps its `X-Config-Version` to 10
4. Next fetch returns `changed: false` until we bump again

## What's coming next

- **PR A (server-side cleanup)** — SS confirmed `model-capabilities.json` is ~80% dead schema (only `supportsReasoning`, `reasoningLevels`, `promptReserveTokens` are consumed). PR A removes the dead fields + the now-redundant per-model `supportsReasoning` in `llm-providers.json`.
- **Templates trial (Anthropic-only)** — separate proposal in `docs/handoffs/ss-per-model-request-schemas-proposal.md`. Boss is weighing in first; nothing for iOS to do until that lands.

## Questions for SS

- Do the per-model temperature values match what you'd want as a picker default? Especially the Kimi mode-specific ones (0.6 vs 1.0).
- Are there other capability constraints iOS needs that we missed in PR B? (E.g., per-model max input tokens beyond `contextWindow`? per-model tokens-per-second hints for UI pacing?)
- Confirm forward-compat works — an older build that doesn't read these fields should keep working unchanged.
