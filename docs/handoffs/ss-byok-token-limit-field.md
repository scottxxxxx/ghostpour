# SS handoff — proposal: add `tokenLimitField` to LLMProviders.json

**Date:** 2026-05-18
**From:** Shoulder Surf (iOS) side
**Subject:** Push the `max_tokens` vs `max_completion_tokens` decision into GP's provider config so iOS stays thin

## TL;DR

OpenAI deprecated `max_tokens` for the GPT-5 family (and the o-series before it) in favor of `max_completion_tokens`. Other OpenAI-compat providers (OpenRouter, Groq, DeepSeek, Kimi, Qwen) still expect `max_tokens`. Today iOS has no way to know which field a given provider wants — the choice has to be hardcoded.

Asking GP to add one field to LLMProviders.json (`tokenLimitField`) so iOS reads the right param name straight from your config instead of branching on `config.id`. Future provider quirks of the same shape become a server-side push instead of a TestFlight build.

## The bug that prompted this

Scott reports HTTP 400 from OpenAI when iOS validates a BYOK key. GP validates the same key successfully because GP's probe doesn't send `max_tokens`. iOS's probe at `LLMService.swift:660` does:

```swift
let body: [String: Any] = [
    "model": probeModel,
    "messages": [["role": "user", "content": "ping"]],
    "max_tokens": 1
]
```

`probeModel` is `config.models.first?.id`. Live GP config has `gpt-5.5` as the default OpenAI model. OpenAI returns:

> `Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.`

Confirmed by reading current `/v1/config/llm-providers` for the openai provider — model list is `gpt-5.5, gpt-5.2, gpt-5-mini` (in that order), all reject `max_tokens`.

## Why iOS can't solve this cleanly today

Two paths, both lossy:

1. **Hardcode the discriminator:** `if config.id == "openai" { body["max_completion_tokens"] = 1 } else { body["max_tokens"] = 1 }`. Works today. Breaks the moment another provider does the same thing (DeepSeek's reasoner already has its own quirks; xAI added new param shapes for grok-4.1). Every quirk = one iOS commit + a TestFlight cycle.

2. **Omit the token cap entirely.** Matches production path's "accept provider defaults" pattern (`LLMService.swift:1296`). But on gpt-5.x with default reasoning, an unbounded `ping` probe can generate hundreds of reasoning tokens per call (~$0.001 per validation). User-initiated, so bounded — but inelegant and the original probe's "cheapest possible call" intent goes away.

Neither preserves the "GP owns wire-shape decisions" pattern you've documented in `reference_gp_wire_contracts`.

## Proposed change

Add an optional string field on each provider in `LLMProviders.json`:

```json
{
  "id": "openai",
  "displayName": "OpenAI",
  "apiFormat": "openai",
  "baseURL": "https://api.openai.com/v1/chat/completions",
  "tokenLimitField": "max_completion_tokens",   // ← new
  ...
}
```

Semantics:

| Value | iOS behavior |
|---|---|
| `"max_tokens"` or absent | iOS sends `"max_tokens": N` in the request body (existing behavior — fully backwards compatible) |
| `"max_completion_tokens"` | iOS sends `"max_completion_tokens": N` instead |
| `null` (explicit) | iOS omits the token cap entirely; lets the provider use its own default |

Recommended initial values:

| Provider | Suggested `tokenLimitField` |
|---|---|
| openai | `"max_completion_tokens"` |
| openrouter | `"max_tokens"` *(or null if you prefer to defer to model-side defaults)* |
| groq, kimi, qwen, deepseek | `"max_tokens"` |
| anthropic | omit — Anthropic requires `max_tokens` and the field name is part of their wire contract, not negotiable. iOS hardcodes it in the Anthropic branch (already correct). |
| gemini | omit — Gemini uses a different body shape entirely; this field doesn't apply. |

Field naming is just a proposal — `tokenLimitField` matches the existing camelCase pattern in your provider config but pick whatever fits your conventions. The semantics are the part we care about.

## iOS commitment

Once you ship this in `/v1/config/llm-providers`, iOS will:

1. Add `tokenLimitField: String?` to `LLMProviderConfig` (Codable, optional, defaults to nil).
2. Replace the hardcoded `"max_tokens": 1` in the probe with:
   ```swift
   if let field = config.tokenLimitField {
       body[field] = 1
   } else if config.apiFormat == "openai" {
       body["max_tokens"] = 1  // backwards compat for older GP configs
   }
   ```
   Or if you'd rather we drop the legacy fallback and require all providers carry the field explicitly, say the word.
3. Apply the same lookup to any future production code path that needs to set a token cap (currently we don't — production OpenAI-compat omits the cap entirely — but Anthropic does set `max_tokens: 4096` and could read this field if/when you'd rather control the value server-side).

One TestFlight cycle from us, then this class of bug is permanently resolved.

## Open questions

1. **Naming.** `tokenLimitField`? `responseTokenLimitParam`? Your call.
2. **Per-provider vs per-model.** OpenAI is currently uniform — all their models in your config use `max_completion_tokens`. Is there any provider where the right field name varies by model? If yes, this field should live on the per-model entry instead of the provider. (I don't see one today.)
3. **Backwards-compat window.** Are you OK with iOS keeping the `apiFormat == "openai" → max_tokens` legacy fallback for one release cycle, or do you want us to require the field be present?
4. **Should this apply to the probe path only, or also future production paths?** Today only the probe sends a token cap on OpenAI-compat. If GP ever wants to push iOS-side cost controls, this same field could drive production. Worth deciding now whether the field is "probe-only" semantically or general-purpose.

## Anything we need to do on our side before you ship

If you'd like us to ship the iOS hotfix today (hardcoded discriminator, Option A from our internal triage) to unblock testers while you implement this, say the word — it's 3 lines, takes 5 min. We'd remove it once `tokenLimitField` lands. Otherwise we'll wait for your change and ship clean.

— SS
