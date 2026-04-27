# Wire contracts

JSON Schema definitions for response shapes that downstream clients
(today: ShoulderSurf iOS) depend on. Use these as the diff target when
making changes to user-facing API responses — if a PR moves the wire
without moving the schema, that's a coordination bug.

## Conventions

- **Source of truth is the wire**, not the schema. Schemas are
  contract artifacts; if they drift from the wire, fix the schema or
  fix the wire — don't pretend the discrepancy doesn't exist.
- **Bump the `$id` version** when making a breaking change to a
  schema. Additive changes (new optional fields) don't require a bump.
- **Co-evolve PRs**: if you change a wire surface, update the matching
  schema in the same PR. CI doesn't enforce this yet — discipline does.
- **Not exhaustive**: only contracts that cross a team boundary live
  here. Internal-only shapes don't need a contract.

## Files

| Schema | Used by | What it defines |
|---|---|---|
| `tier-row-item.schema.json` | `/v1/tiers` `feature_items[]` and `status_items[]` rows | Unified row schema (`label`, optional `value`, `icon`, optional `state`) |
| `ai-tier.schema.json` | `/v1/chat`, `/v1/meetings/{id}/report` (POST and GET cached) | Tier-derived `ai_tier` field; values, semantics, null fallback |

## Why this directory exists

Came out of the 2026-04-27 deploy where an `ai_tier` divergence between
GP and ShoulderSurf iOS surfaced after the deploy was live (Plus
subscribers briefly saw raw Haiku attribution because iOS hadn't
adopted the abstraction yet, contrary to what we'd been told). A
versioned, repo-tracked contract artifact for these wire surfaces would
have surfaced the divergence at PR-review time, not deploy time.
