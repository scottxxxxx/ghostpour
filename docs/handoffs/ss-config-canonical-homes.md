# Proposal: canonical homes for per-model config fields (SS team)

## Why we're asking

PR #183 added 7 per-model capability fields to `llm-providers.json` (`maxOutputTokens`, `temperatureDefault`, `maxImagesPerRequest`, `streamingSupported`, `toolUseSupported`, `cacheControlSupported`, `serverManaged`). At the same time, your audit told us `model-capabilities.json` is ~80% dead schema from iOS's perspective — you only consume `supportsReasoning`, `reasoningLevels`, `promptReserveTokens`.

So we now have two iOS-facing configs with overlapping fields and unclear ownership. Before we do PR A (the cleanup pass), we want SS to weigh in on **where each field should canonically live**, because a few different shapes are reasonable and you're the consumer.

This is *separate* from the templates trial in `ss-per-model-request-schemas-proposal.md`. Templates are about *how* iOS calls a provider directly (BYOK wire shape). This doc is about *what metadata about each model* lives where (capability flags, display strings, routing hints).

## Current field inventory

Every per-model field in either file, who reads it today, and where it lives:

| Field | `llm-providers.json` | `model-capabilities.json` | iOS reads? | Server reads? |
|---|---|---|---|---|
| `id` / model key | yes | yes (object key) | yes | yes |
| `displayName` | yes | implicit (`provider` only) | yes (picker) | no |
| `description` | yes | yes | ? | no |
| `isDefault` | yes | no | yes (picker) | no |
| `supportsVision` | yes | no (uses `supportsImages`) | yes? | no |
| `supportsImages` | no | yes | ? | no |
| `contextWindow` | yes (int) | yes (string "1M") | yes? | yes |
| `inputCostPerMillion` | yes | yes | no (per [[reference_ss_no_badge_or_cost]]) | yes |
| `outputCostPerMillion` | yes | yes | no | yes |
| `litellmKey` | yes | no | no | yes |
| `supportsReasoning` | yes (PR B) | yes | **yes** | yes |
| `reasoningLevels` | no | yes | **yes** | yes |
| `promptReserveTokens` | no | yes (audit says) | **yes** | maybe |
| `maxOutputTokens` | yes (PR B) | no | yes | yes |
| `temperatureDefault` | yes (PR B) | no | yes | yes |
| `maxImagesPerRequest` | yes (PR B) | no | yes | no |
| `streamingSupported` | yes (PR B) | no | yes | no |
| `toolUseSupported` | yes (PR B) | no | yes (future) | no |
| `cacheControlSupported` | yes (PR B) | no | yes (BYOK Anthropic) | yes |
| `serverManaged` | yes (PR B) | no | yes | yes |
| `contextSlots` | no | yes | no | **yes (routing)** |
| `contextQuilt` | no | yes | no | **yes (routing)** |
| `splitModelSummary` | no | yes | no | **yes (routing)** |
| `estimatedAvailableTokens` | no | yes | no | **yes (routing)** |
| `weights` | no | yes | no | no (informational) |
| `requiredParams` | no | yes | no | no (informational) |
| `promptPlacement` | no | yes | no | no (informational) |

A few things jump out:

1. **`model-capabilities.json` is actually two files in a trench coat.** It carries iOS-facing capability flags (3 fields you read) and server-side routing intelligence (`contextSlots`, `contextQuilt`, etc. that drive how GP composes prompts for `cloudzap.auto`). Those two audiences shouldn't share a JSON.
2. **`contextWindow` has different types in the two files** (`1000000` vs `"1M"`). One of them is wrong.
3. **`supportsVision` vs `supportsImages`** — both exist, both mean roughly the same thing. We should pick one name.
4. **Several fields in `model-capabilities.json` are pure documentation** (`weights`, `requiredParams`, `promptPlacement`) — nobody reads them at runtime.
5. **`reasoningLevels` and `promptReserveTokens` are iOS-critical but only live in `model-capabilities.json`.** If we collapse files, they need a home.

## Three directions we could go

### Option A — Single iOS-facing config

Collapse everything iOS reads into `llm-providers.json`. Move `reasoningLevels` and `promptReserveTokens` over. Delete `model-capabilities.json` entirely as an iOS-facing file (move the routing intelligence into a new server-internal config, e.g., `config/internal/model-routing.json`, that's not published to iOS at all).

**Pros:** One file, one fetch, no overlap. iOS reads `llm-providers.json` and that's it. The server-only routing intelligence stops being shipped over the wire to clients that don't need it.

**Cons:** Bigger iOS-side migration — every accessor that reads `model-capabilities.json` today has to move to `llm-providers.json`. Three fields, but every call site changes.

### Option B — Two iOS-facing configs with clean ownership

Keep both files but split by purpose:
- `llm-providers.json` = **routing + wire** (auth, endpoint, IDs, what bodies to send, per-model wire caps)
- `model-capabilities.json` = **UX hints** (`reasoningLevels`, `promptReserveTokens`, anything that drives iOS UI choices)

Drop everything in `model-capabilities.json` that isn't UX-related. Move the server-side routing fields (`contextSlots`, `contextQuilt`, etc.) into an internal server-only config that doesn't get published to iOS.

**Pros:** Smaller migration — iOS keeps reading `model-capabilities.json` for the 3 fields you already use; we just trim the noise around them. Clean conceptual split.

**Cons:** Two configs to fetch, version, cache. The split is a bit arbitrary (why is `reasoningLevels` UX but `temperatureDefault` wire? both are per-model behavioral hints).

### Option C — Status quo + cleanup only (PR A as currently scoped)

Strip dead fields from `model-capabilities.json`, drop redundant `supportsReasoning` from `llm-providers.json` (canonical = `model-capabilities.json`), pin schemas with tests, done.

**Pros:** Smallest blast radius. No iOS code changes beyond what PR B already implied.

**Cons:** Doesn't fix the fundamental "two files, overlapping data" problem. Next time we add a field we'll have the same "where does this go" debate.

## Our recommendation

**Option A** if you're up for the iOS migration. The fact that `model-capabilities.json` mixes iOS capability flags with server-side routing intelligence is a code smell — those two audiences will keep drifting apart. One file per audience is cleaner long-term.

**Option B** if Option A's migration is too much churn right now. It's a real improvement over status quo and the migration is bounded.

**Option C** is the safe shipping play but doesn't pay down the structural debt.

## What gets deleted regardless of option

These fields in `model-capabilities.json` are unread anywhere we can find:

- `weights` (informational, "Closed" / "Open")
- `requiredParams` (informational string)
- `promptPlacement` (informational string)

We're 90% sure they're dead. Confirm with grep on the SS iOS repo before we strip them.

## What stays server-only regardless of option

These fields are read by GP's router (specifically by `/v1/chat`'s model selector for `cloudzap.auto`) and have no business being in an iOS-facing config:

- `contextSlots`
- `contextQuilt`
- `splitModelSummary`
- `estimatedAvailableTokens`

We move these to a new `config/internal/model-routing.json` that isn't exposed via `/v1/config/*` endpoints. iOS never sees them; we stop paying the bandwidth to ship them on every config fetch.

## Other normalization decisions (any option)

Whatever shape we land on:

- **Pick one of `supportsVision` / `supportsImages`.** Probably `supportsVision` (PR B uses that). Aliasing is a [[feedback_no_backward_compat_preprod]] violation — we should just pick.
- **`contextWindow` is an int.** Drop the `"1M"` string form. iOS does whatever display formatting it wants.
- **Cost fields stay server-side.** Per [[reference_ss_no_badge_or_cost]], iOS doesn't render cost. We use cost fields server-side for budget gate and routing. They shouldn't ship to iOS at all — move to `config/internal/model-routing.json`.

## Migration shape (Option A)

If SS picks A:

1. PR A1 — Add `reasoningLevels`, `promptReserveTokens` to `llm-providers.json` per model. Bump version to 11. SS handoff documents the move.
2. PR A2 — iOS PR: read those two fields from `llm-providers.json` instead of `model-capabilities.json`. Ship.
3. PR A3 — Server PR: stop publishing `model-capabilities.json` as an iOS-facing config. Move `contextSlots` / `contextQuilt` / `splitModelSummary` / `estimatedAvailableTokens` to `config/internal/model-routing.json`. Remove the `/v1/config/model-capabilities` endpoint.

Steps 1+2 can land same-day. Step 3 lands after iOS no longer fetches `model-capabilities.json`.

## Open questions for SS

1. **Which option do you prefer?** A, B, or C. We can implement any of them; the choice is about how much iOS-side migration you want right now.

2. **Confirm the audit.** Re-grep the iOS repo for accessors against `model-capabilities.json` and `llm-providers.json`. Tell us:
   - Every field name dereferenced from each file
   - Anything you'd want kept that's not in our current "iOS reads" column
   - Whether `description` is rendered anywhere (we marked it `?`)
   - Whether `contextWindow` is rendered (we marked it `?`)

3. **`supportsImages` vs `supportsVision`** — which one is iOS reading today? We'll converge on whichever name you're already using.

4. **Are the three model-capabilities fields you read (`supportsReasoning`, `reasoningLevels`, `promptReserveTokens`) tightly coupled, or could they live in separate files?** Asking because we could move `reasoningLevels` to `llm-providers.json` (where the related `supportsReasoning` flag lives) but keep `promptReserveTokens` somewhere else if it's logically different.

5. **`weights` / `requiredParams` / `promptPlacement` — confirm dead.** We don't think anything reads these but we want SS to confirm before we delete them.

## Suggested next step

If SS picks A or B, we sequence PR A around their choice. If C, we ship PR A as currently scoped this week and revisit later.

Whichever way, **the templates trial (Anthropic-only) proceeds in parallel** — it's about request *bodies*, not metadata, and it's boss-gated separately.
