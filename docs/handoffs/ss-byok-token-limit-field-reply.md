# SS reply — `tokenLimitField` clarifying question

**Date:** 2026-05-18
**From:** Shoulder Surf (iOS) side
**Subject:** Confirming `null` for OpenAI-compat providers + agreeing to sequencing
**Replies to:** `ss-byok-token-limit-field.md` GP response (today)

## Direct answer

> *"Are openrouter / groq / kimi / qwen / deepseek confirmed to require max_tokens, or do they accept it optionally? If optional, null is cleaner than carrying the field."*

**`null` for all five.** None of them require `max_tokens` — they all accept it optionally and fall back to the model's built-in default when omitted. Verified against their OpenAI-compat docs; also matches iOS production behavior already: `LLMService.swift:1296` comment explicitly says we omit `max_tokens` in the production chat body and accept provider defaults. The probe was the lone exception, and we were doing the wrong thing on OpenAI proper anyway.

Cost note for the probe: omitting the cap means OpenAI-compat probes can return 100s of tokens of response for the "ping" prompt instead of being capped at 1. User-initiated only ("Test Key" button), so cost is bounded by tap frequency. Acceptable.

## Final shape we'll consume

```json
{
  "openai":    { "tokenLimitField": "max_completion_tokens" },
  "openrouter": { "tokenLimitField": null },
  "groq":      { "tokenLimitField": null },
  "kimi":      { "tokenLimitField": null },
  "qwen":      { "tokenLimitField": null },
  "deepseek":  { "tokenLimitField": null }
  // anthropic, gemini: field absent (wire shape owns it)
}
```

iOS read logic (the legacy fallback is dropped per your guidance, matching pre-prod no-aliases policy):

```swift
// Build base body
var body: [String: Any] = ["model": probeModel, "messages": [...]]

// Apply token cap iff provider declares one
if let field = config.tokenLimitField {
    body[field] = 1
}
```

## Sequencing — confirmed

We'll wait for:
1. GP PR merge
2. Your `POST /webhooks/admin/config/llm-providers/sync-from-bundle` run
3. We verify the new field lands by hitting `GET /v1/config/llm-providers` and checking the `openai` entry

Then we ship the iOS read in one TestFlight cycle. No hotfix in between.

## On the rollout webhook trap

Appreciate the heads-up — this is the same pattern that bit us with `reasoningLevels` earlier (live config one rev behind the bundle until a manual sync fires). Worth noting we have it documented internally in `reference_gp_config_sync` as "PR-merge ≠ live; ping GP to sync." Saving you the ping next time once the self-service admin endpoint lands.

— SS
