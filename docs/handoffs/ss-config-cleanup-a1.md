# SS handoff — PR A1 (Option A consolidation, step 1)

Landed: 2026-05-12. `llm-providers.json` is at **version 11** across all three locales.

This is step 1 of the Option-A migration we agreed on in `ss-config-canonical-homes.md`. PR A2 (your iOS side) can now go. PR A3 (server-side removal of `model-capabilities.json` as an iOS-facing config) lands once A2 has rolled forward.

## What shipped

### Three new fields per model

Each `providers[*].models[*]` entry now carries:

| Field | Type | Source |
|---|---|---|
| `reasoningLevels` | `array<string> \| null` | Moved over from `model-capabilities.json` verbatim. Provider-native vocabularies — see [[ss-reasoning-rev2]] for the rationale. |
| `promptReserveTokens` | `int \| null` | New. All values are `null` for now (every model falls back to the file-level default — matches today's behavior). |

### One new top-level field

```json
{
  "version": 11,
  "defaultPromptReserveTokens": 8000,
  "providers": [...]
}
```

`defaultPromptReserveTokens` is the file-level fallback iOS reads when a model's per-model `promptReserveTokens` is `null` (which is every model today). Key name confirmed per your ask.

iOS getter pattern:

```swift
func promptReserveTokens(for model: LLMModelConfig) -> Int {
    return model.promptReserveTokens
        ?? LLMProvidersConfig.shared.defaultPromptReserveTokens
}
```

### Reconciled `supportsReasoning` mismatches

We caught 6 routes where `llm-providers.json` had `supportsReasoning: true` but `model-capabilities.json` (the iOS-canonical source today) had it `false`. iOS today gates the picker on the AND of `supportsReasoning && !reasoningLevels.isEmpty`, so the picker correctly hid — but once your PR A2 swaps to reading from `LLMModelConfig`, those models would suddenly start trying to render a picker with `reasoningLevels: null`.

Fixed in this PR so A2 stays a pure relocation, not a behavior change:

- `anthropic:claude-haiku-4-5-20251001` → `false`
- `openrouter:anthropic/claude-haiku-4.5` → `false`
- `kimi:kimi-k2-thinking` → `false`
- `qwen:qwen-plus` → `false`
- `qwen:qwen-flash` → `false`
- `qwen:qwen3-max` → `false`

(These are all models where reasoning happens server-side via budget_tokens or other non-picker paths — no string vocabulary for iOS to show.)

## What this means for PR A2

The three callsites you flagged become:

```swift
// LLMService.swift:744 (was: ModelCapabilitiesStore.reasoningLevels(for: model))
let levels = model.reasoningLevels ?? []
let canShowPicker = model.supportsReasoning && !levels.isEmpty

// ProjectChatSection.swift:1751 (was: ModelCapabilitiesStore.promptReserveTokens(for: ...))
let reserve = model.promptReserveTokens
    ?? LLMProvidersConfig.shared.defaultPromptReserveTokens

// ModelCapabilities.swift:198 — the file-level fallback
// becomes config.defaultPromptReserveTokens; ModelCapability struct goes away.
```

Plus the two `ModelCapabilitiesStore.reload()` calls in `SettingsView.swift:1075` and `ShoulderSurfApp.swift:435` — those go away when the struct is deleted.

The cost-fallback path in `effectivePricing()` and the `LLMResponseItem.estimatedCost` plumbing can be dropped in the same commit (per your audit, they're vestigial post-Apr-27 UI ripout).

## Prod sync (boss does this)

Same pattern as PR #183. From `/opt/ghostpour` on the GCP VM:

```python
import json, subprocess
key = subprocess.check_output(
    ["sudo", "grep", "^CZ_ADMIN_KEY", "/opt/ghostpour/.env.prod"]
).decode().strip().split("=", 1)[1]

for slug in ["llm-providers", "llm-providers.es", "tr-llm-providers"]:
    subprocess.check_output([
        "curl", "-sS", "-X", "POST",
        "-H", f"X-Admin-Key: {key}",
        "-H", "Content-Type: application/json",
        "-d", '{"keys":["providers","version","defaultPromptReserveTokens"]}',
        f"https://cz.shouldersurf.com/webhooks/admin/config/{slug}/sync-from-bundle",
    ])
```

(Note the extra key `defaultPromptReserveTokens` in the keys array — since it's a new top-level field, the sync-from-bundle keys list needs to include it.)

Verify after:
```
curl -H "X-Admin-Key: $KEY" https://cz.shouldersurf.com/webhooks/admin/config/llm-providers | jq '.data.version, .data.defaultPromptReserveTokens'
```
Should return `11` and `8000`.

## Versioning recap

| Version | Date | Change |
|---|---|---|
| 9 | pre-2026-05-12 | Provider-level capability fields only. |
| 10 | 2026-05-12 (PR #183) | Added 7 per-model fields: `maxOutputTokens`, `temperatureDefault`, `maxImagesPerRequest`, `streamingSupported`, `toolUseSupported`, `cacheControlSupported`, `serverManaged`. |
| 11 | 2026-05-12 (this PR) | Added `reasoningLevels`, `promptReserveTokens` per model + top-level `defaultPromptReserveTokens`. Reconciled 6 `supportsReasoning` mismatches with `model-capabilities.json`. |

## Open items (no action needed from SS yet)

- **PR A3 — remove `model-capabilities.json` as iOS-facing config.** Lands after A2 rolls forward. Server-side routing intelligence (`contextSlots`, etc.) moves to `config/internal/model-routing.json`; the `/v1/config/model-capabilities` endpoint goes away. We'll send a separate handoff once that's ready.
- **Templates trial (Anthropic-only).** Still boss-gated. Proposal at `ss-per-model-request-schemas-proposal.md` is separate from this work arc.
