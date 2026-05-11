# SS handoff — reasoning picker vocabulary rev 2

Two server-side PRs landed end-to-end (#174 + #175) on 2026-05-11. Server is live; iOS rebuild needed before the picker behaves correctly. This doc consolidates both PRs into a single iOS-team brief.

For the authoritative spec see `docs/wire-contracts/reasoning-control.md`.

## What changed at the protocol level

The `reasoning` field on `/v1/chat` went from `off | low | medium | high` to **`default | minimal | low | medium | high`**.

- **`off` → `default`** — rename. Most providers don't change behavior (still "omit reasoning field, let provider decide"). On binary-toggle providers (Kimi, Qwen, DeepSeek) "default" still force-disables thinking, just like the old `off` did.
- **`minimal` is new** — explicit "request the smallest non-default native level." Real on OpenAI gpt-5.x and Gemini 3 Flash / Flash-Lite. Hidden in the picker elsewhere.
- **`off` is rejected with 422.** No backward-compat alias. Old builds break — rebuild before testing.

## What iOS needs to do

### Path 1: GP-managed (`provider: "cloudzap"`)

Picker is data-driven from `model-capabilities.json.reasoningLevels`. With config v7 deployed, the final per-model button counts are:

| Models | Buttons rendered |
|---|---|
| OpenAI gpt-5.x (5 models) | 5 — Default, Minimal, Low, Medium, High |
| Anthropic Claude 4.x (3 models) | 4 — Default, Low, Medium, High |
| Gemini 3 Flash / Flash-Lite (2 models) | 5 — Default, Minimal, Low, Medium, High |
| Gemini 3 Pro (1 model) | 4 — Default, Low, Medium, High |
| Grok 4 / 4.1-fast (2 models) | **4 — Default, Low, Medium, High** *(was 2; expanded in #175 — Grok's native API supports 4 levels)* |
| Kimi / Qwen / DeepSeek (8 models) | 2 — Default, High |
| Foundation Models | (hidden) |

**Label changes:**
- "Off" → **"Default"**
- New: **"Minimal"** (when present in the array)

If your picker iterates `reasoningLevels` and renders one button per entry with the value as the label key, the rebuild is just the localized string table.

### Path 2: OpenRouter BYOK (iOS → OR direct)

GP isn't in this path. Mapping per the wire contract:

| iOS picker value | OpenRouter request body |
|---|---|
| `default` | **Omit `reasoning` block entirely** |
| `minimal` | `"reasoning": { "effort": "minimal" }` |
| `low` | `"reasoning": { "effort": "low" }` |
| `medium` | `"reasoning": { "effort": "medium" }` |
| `high` | `"reasoning": { "effort": "high" }` |

This replaces today's iOS behavior where "Off" → omit. The semantics for `default` are the same; the new work is wiring `minimal` → `effort: "minimal"` (OR supports this for gpt-5 + Gemini 3 Flash).

## Server-side notes (FYI — no iOS impact, but useful context if anything looks off)

These were broken in PR #174 and corrected in PR #175:

1. **Claude Opus 4.7** used to 400 on any non-default level — server was sending the legacy `thinking: {enabled, budget_tokens}` shape, which Anthropic rejects for Opus 4.7. Now uses the modern `output_config: {effort: ...}` + `thinking: {type: "adaptive"}` shape. Sonnet 4.6 also migrated to this path (recommended by Anthropic). Haiku 4.5 stays on the legacy `budget_tokens` shape (Anthropic explicitly excludes Haiku from the effort path).
2. **Kimi K2.x** had the wrong field name (`enable_thinking: bool` instead of `thinking: {type: "enabled"/"disabled"}`). Fixed.
3. **Qwen 3.x** same issue — was sending `enable_thinking: bool`, now sends `thinking_budget: int` per Qwen's actual API.
4. **Grok 4 / 4.1-fast** were under-exposed at `[low, high]` — Grok's native API supports `none/low/medium/high`. Picker expanded to `[default, low, medium, high]`.

All iOS-visible: the picker on Grok now has 4 buttons instead of 2.

## Test plan

1. **Build with rev 2 vocabulary** — confirm picker labels: "Default" everywhere "Off" used to be; "Minimal" appears on gpt-5.x and Gemini 3 Flash/Flash-Lite.
2. **Send each level on Claude Opus 4.7** — was 400-ing before #175, should now succeed end-to-end. Check `usageMetadata` for thinking tokens scaling with the effort level.
3. **Send each level on Grok 4** — confirm all 4 buttons work; medium is the new one we couldn't expose before.
4. **Send each level on Kimi K2-Thinking** — confirm Default actually disables thinking (check token count is short for Default and longer for High).
5. **Send each level on Qwen-Max** — same, confirm Default = no thinking tokens, High = `thinking_budget: 16384` worth of reasoning.
6. **OR-direct path on Gemini 3 Flash** — pick "Minimal" via OR, confirm response has fewer reasoning tokens than "High". Pick "Default", confirm no `reasoning` block in the iOS-sent request and OR's response reflects the provider's default.
7. **Legacy 422 check** — old iOS build sending `reasoning: "off"` to `/v1/chat` returns 422.

## References

- Wire contract (canonical): `docs/wire-contracts/reasoning-control.md`
- Server PRs: #174 (vocabulary), #175 (per-provider translation fixes)
- Config: `model-capabilities.json` v6 persistent / v7 bundled (Grok now 4-level)
- Server-side translation: `app/services/providers/reasoning.py`

## Unverified — flag if you see weirdness in testing

Noted in PR #175 as not-yet-verified via live API:

- OpenAI `gpt-5-mini` / `gpt-5-nano` — assumed full 4-level `minimal/low/medium/high`; could be restricted
- DeepSeek V4 Flash vs Pro — behavioral differences unverified
- Kimi `k2-turbo-preview` — schema may differ from K2.5/K2-Thinking
- Gemini 3 Flash-Lite specific `minimal` support — Google grouped Flash + Flash-Lite together but didn't separately call out Flash-Lite for `minimal`

If any produce 4xx errors or unexpected behavior in testing, ping back with the request_id from GP's response. Live-API smoke tests are planned for these but haven't run yet.
