# Proposal: per-model request templates (SS team)

## The idea

GP publishes a per-(provider, model) **request template** as config (same pattern as protected-prompts, feature-highlights, model-capabilities). iOS fetches it, fills in user content + their BYOK key, and **calls the provider directly**. GP is not the middleman.

This is exactly what iOS already does for OpenRouter BYOK — extended to OpenAI, Anthropic, Gemini, xAI, Moonshot, DeepSeek, Alibaba directly.

**The operational win:** when a provider changes their API or we need to fix a wire-shape bug, we update the template once on GP. Every iOS install picks up the corrected template at next launch — no app update, no App Store review, no field stragglers running broken bodies.

## What GP would publish

A new config slug, e.g., `model-templates.json`, keyed by model ID. Each entry contains everything iOS needs to construct the request:

```json
{
  "claude-opus-4-7": {
    "endpoint": "https://api.anthropic.com/v1/messages",
    "method": "POST",
    "auth": {
      "header": "x-api-key",
      "prefix": ""
    },
    "extraHeaders": {
      "anthropic-version": "2023-06-01",
      "content-type": "application/json"
    },
    "bodyTemplate": {
      "model": "claude-opus-4-7",
      "max_tokens": "{{max_tokens}}",
      "system": [{"type": "text", "text": "{{system_prompt}}"}],
      "messages": [
        {
          "role": "user",
          "content": "{{user_content_blocks}}"
        }
      ],
      "thinking": "{{thinking_block_or_omit}}",
      "output_config": "{{output_config_or_omit}}"
    },
    "slots": {
      "max_tokens": {"type": "int", "default": 4096},
      "system_prompt": {"type": "string", "required": false},
      "user_content_blocks": {
        "type": "array",
        "item": "anthropic_content_block",
        "min_items": 1
      },
      "thinking_block_or_omit": {
        "type": "object_or_omit",
        "from_reasoning_level": {
          "default": "omit",
          "*": {"type": "adaptive"}
        }
      },
      "output_config_or_omit": {
        "type": "object_or_omit",
        "from_reasoning_level": {
          "default": "omit",
          "*": {"effort": "{{reasoning_level}}"}
        }
      }
    },
    "imageBlock": {
      "type": "image",
      "source": {"type": "base64", "media_type": "image/jpeg", "data": "{{base64}}"}
    },
    "forbidden": {
      "thinking.type == 'adaptive'": ["temperature", "top_p", "top_k"]
    }
  },
  "gpt-5.5": {
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "method": "POST",
    "auth": {"header": "Authorization", "prefix": "Bearer "},
    "extraHeaders": {"content-type": "application/json"},
    "bodyTemplate": {
      "model": "gpt-5.5",
      "messages": "{{messages}}",
      "max_completion_tokens": "{{max_tokens}}",
      "reasoning_effort": "{{reasoning_level_or_omit}}"
    },
    "slots": {
      "messages": {"type": "openai_messages_array"},
      "max_tokens": {"type": "int", "default": 4096},
      "reasoning_level_or_omit": {
        "type": "string_or_omit",
        "from_reasoning_level": {"default": "omit", "*": "{{reasoning_level}}"}
      }
    },
    "imageBlock": {
      "type": "image_url",
      "image_url": {"url": "data:image/jpeg;base64,{{base64}}"}
    }
  }
}
```

(Sketch — the actual schema details get refined once we know iOS's parser preferences.)

## What iOS does

1. Fetches `/v1/config/model-templates` at launch with cache + version header (same pattern as `tiers`, `protected-prompts`, etc.)
2. When user picks a model, loads that model's template
3. Fills in slots from user input (message, system prompt, images, reasoning level)
4. Adds the user's BYOK API key to the auth header per the template's `auth.header`/`auth.prefix`
5. POSTs directly to `template.endpoint`
6. Handles the response per the provider's documented format

GP is never in the request path for direct calls.

## What GP keeps publishing alongside

`model-capabilities.json` continues to hold the metadata iOS needs to drive the UI:
- `reasoningLevels` (already published — drives picker buttons)
- `supportsVision`, `supportsImages`
- `contextWindow`, `inputCostPerMillion`, `outputCostPerMillion`
- Pricing display, gauge denominators, etc.

The new `model-templates.json` is the *wire-shape* config. The two configs are siblings.

## Operational benefits

- **Hot-fix wire-shape bugs.** Anthropic deprecates `budget_tokens` on Opus 4.7? We push a template update; every install picks it up next launch. No app store deployment.
- **Add new providers fast.** A new provider supported by enough users? Publish a template; existing iOS code reads it; no app rev.
- **Encode quirks centrally.** Opus 4.7 rejects `temperature` when thinking is adaptive — that lives in the template's `forbidden` block. iOS reads, doesn't send.
- **One source of truth.** Our reasoning-control wire-contract findings from the last two days all become template entries instead of in-app special cases.

## Open questions for SS

Things we need to decide together before designing the schema in detail:

1. **Scope of templates** — which providers does iOS want to support direct? Today iOS does OR direct. Adding others means iOS needs key management for each (OpenAI, Anthropic, Gemini, xAI, Moonshot, DeepSeek, Alibaba). Settings UI for "paste your provider keys" multiplies. Is that planned, or do we start with a subset?

2. **Template format / engine** — string-substitution (`{{slot}}`) or a richer expression DSL? The Anthropic adaptive-thinking case ("when reasoning_level != default, splice this block in; otherwise omit") needs conditional substitution. How much logic does iOS want to interpret vs. how much should be flat strings?

3. **Slot vocabulary** — what's the canonical name for "user message text"? "system prompt"? "images"? "reasoning level"? Each model would use these names in its slots so iOS knows what to fill from its UI state. Want to define the slot dictionary up front.

4. **Image attachment** — Anthropic wants `{"type": "image", "source": {"type": "base64", ...}}`, OpenAI wants `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`, Gemini wants `inlineData: {mimeType, data}`. Templates encode the per-provider block; iOS substitutes the base64 content. Sound right?

5. **System prompt placement** — Anthropic puts it in a separate `system` field; Gemini in `systemInstruction`; OpenAI in a message with `role: "system"`. The template wraps it correctly per provider; iOS just supplies the text. OK?

6. **Reasoning level integration** — already published per model via `reasoningLevels`. The template references `{{reasoning_level}}` (filled from the user's picker choice) plus knows how to splice it into the body (e.g., Anthropic's `output_config.effort` vs OpenAI's `reasoning_effort`). Should we unify `model-capabilities` and `model-templates`, or keep them separate?

7. **`default` reasoning handling** — when iOS passes `reasoning_level = "default"`, the template should omit the relevant fields. Need a way to express "omit this slot when the source value is X." The example sketch uses `from_reasoning_level: {"default": "omit", "*": <expr>}` — open to better notation.

8. **Auth scheme variants** — most providers use `Authorization: Bearer <key>` or `x-api-key: <key>`. Google puts the key in the URL (`?key=...`). The template needs to encode this. Want to design a small enum of auth modes.

9. **Response parsing** — providers vary in response shape too (Anthropic `content[]`, OpenAI `choices[]`, Gemini `candidates[]`). Does the template encode parsing rules, or does iOS keep model-aware response handling out of scope?

10. **Versioning + cache** — `model-templates.json` carries a `version` integer (like our other configs). iOS sends `X-Config-Version` header and gets `changed: false` when current. Lifecycle: how often does iOS check? Same as protected-prompts (on app foreground)?

11. **GP-managed path** — for subscription users hitting GP-managed models (not BYOK), we still want GP's `/v1/chat` to handle the call (CQ recall, budget gate, audit log, search caps). The template proposal is BYOK-only, right? Or did you want it to apply to GP-managed too somehow?

12. **Failure modes** — provider returns 4xx because the template is stale or wrong. How should iOS surface this? Retry with refreshed template? Show an error? GP doesn't see the request so we can't log it server-side.

## Suggested next step

If SS thinks the direction is right: pick one provider (Anthropic is probably the most useful — it's where the wire-shape complexity lives) and design + ship `model-templates.json` for just those models as a trial. Once the schema feels solid, expand to OpenAI/Gemini/etc.

If SS thinks it's overkill: we keep the current adapter approach for GP-managed paths, and for BYOK we keep doing what we do today (OR direct + a separate code path per provider iOS supports).
