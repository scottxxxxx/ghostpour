# Next-session punch list — written 2026-05-12 (post-PR-#184)

Rolling doc. Read at the start of each session, replace at the end. The most-recent shipped state is `git log --oneline main -10` and `MEMORY.md`; this doc is *what to pick up next*, not what's already done.

## Where we are right now (2026-05-12, post-PR-A1)

This session SS confirmed **Option A** consolidation (collapse iOS-facing per-model fields into `llm-providers.json`; relegate `model-capabilities.json` to server-only routing intelligence eventually deleted). The sequence is A1 → A2 → A3.

Just opened **PR #184 (PR A1)** — added `reasoningLevels` + `promptReserveTokens` per model and top-level `defaultPromptReserveTokens: 8000`. Bumped `llm-providers.json` v10 → v11. Reconciled 6 `supportsReasoning` mismatches so SS's A2 stays a pure relocation.

PR #183 (PR B) is **merged but not yet prod-synced**. PR #184 needs review + merge + prod-sync. The two can be synced together once #184 merges.

See:
- `MEMORY.md` → `project_2026_05_12_per_model_fields.md` (updated with both PR B + A1 + Option A plan)
- `docs/handoffs/ss-config-canonical-homes.md` (the proposal SS responded to)
- `docs/handoffs/ss-config-cleanup-a1.md` (what shipped in A1; what SS does in A2)
- `docs/wire-contracts/llm-providers-fields.md` (authoritative spec, now covers all 9 per-model + 1 top-level field)

---

## 1. **[BLOCKER for SS reading new fields] Prod sync — combine #183 (v10) + #184 (v11)** — user-driven, not Claude

Both PRs need to land in the persistent config store on the VM. Easiest: wait for #184 to merge, then one sync run picks up everything (the sync command pushes whatever's in the bundled JSONs).

Three slugs need `sync-from-bundle`:
```
llm-providers
llm-providers.es
tr-llm-providers
```

PR #184 added a new top-level field, so the `keys` array needs `defaultPromptReserveTokens` in addition to `providers` + `version`. From `/opt/ghostpour` on the GCP VM:

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

**Verify after sync:**
```
curl -H "X-Admin-Key: $KEY" https://cz.shouldersurf.com/webhooks/admin/config/llm-providers \
  | jq '.data.version, .data.defaultPromptReserveTokens'
```
Should return `11` and `8000`.

This is on the boss; Claude shouldn't attempt it.

---

## 2. **PR #184 review + merge + SS handoff drop** — user-driven

PR #184 is open. After merge:
- Drop `docs/handoffs/ss-config-cleanup-a1.md` to SS team (Slack/email) along with the PR link.
- Highlight: the new top-level `defaultPromptReserveTokens` key, the 6 `supportsReasoning` reconciliations, and the green light for them to start PR A2 once prod-synced.

If we drop the handoff *before* prod sync, SS fetches v10 and won't see the new fields. Order matters: merge → sync → notify.

---

## 3. **PR A2 — on SS** (no Claude work, but unblocks A3 when done)

Their estimate was half a day. Three iOS call-sites move from `ModelCapabilitiesStore` to `LLMModelConfig` + file-level fallback:

- `LLMService.swift:744`
- `ProjectChatSection.swift:1751`
- `ModelCapabilities.swift:198`

Plus deletion of `ModelCapability` struct + `ModelCapabilitiesStore.reload()` call sites + the vestigial `effectivePricing()` cost-fallback path + `LLMResponseItem.estimatedCost` plumbing.

Once SS confirms A2 has shipped to TestFlight / their internal builds *and* their A2-or-later iOS no longer fetches `/v1/config/model-capabilities`, PR A3 unblocks.

---

## 4. **PR A3 — model-capabilities.json removal** (us, after A2 rolls forward)

### Scope

1. Add `config/internal/model-routing.json` — server-only config carrying `contextSlots`, `contextQuilt`, `splitModelSummary`, `estimatedAvailableTokens` keyed by model id. Not published via any `/v1/config/*` endpoint.
2. Update GP's router (anywhere that reads `model-capabilities.json` server-side) to read from the new internal config instead. Search: `grep -rn "model-capabilities" app/` to find all reads.
3. Remove the `/v1/config/model-capabilities` route (whatever serves it today).
4. Delete `config/remote/model-capabilities.json`, `model-capabilities.es.json`, `tr-model-capabilities.json`.
5. Delete `ModelCapability` server-side dataclass if one exists.
6. Update `tests/test_reasoning_levels_per_model.py` — it currently reads model-capabilities.json; either retarget at llm-providers.json or delete (duplicated by `test_llm_providers_per_model_fields.py::test_reasoning_levels_consistent_with_supports_reasoning`).
7. CHANGELOG + new SS handoff `docs/handoffs/ss-config-cleanup-a3.md` confirming removal.

### Pre-work

Before A3 lands, verify with SS that *no iOS build in active TestFlight* still reads `model-capabilities.json`. The endpoint removal is the breaking edge — if an old build fetches it and 404s, the picker silently breaks.

### Suggested gating

Soak window: leave the endpoint serving for at least one SS release cycle after A2 ships, then remove.

---

## 5. **Templates trial — Anthropic-only BYOK request templates** (boss-gated)

Unchanged from previous list. Draft proposal at `docs/handoffs/ss-per-model-request-schemas-proposal.md` shipped in PR B. Boss said: "I am the boss of you and SS. I will weigh in first" — so the proposal is **not yet shared with SS**. Boss reviews; if greenlit, then SS conversation happens.

Boss also said: "Anthropic since that is what we use for SS AI" — Anthropic-only first.

The 12 open questions in the proposal doc all stand. Nothing for Claude to do until boss makes a call.

---

## 6. **Open question — provider-level `temperatureDefault` fallback**

Status: orphaned but still open. Today every provider in `llm-providers.json` carries a provider-level `temperatureDefault: 0.7` (0.6 for Kimi). After PR B every model carries its own; after PR A1 the picture is even clearer. The provider-level value is dead weight.

Two options for a small future PR:
- **(a)** Drop the provider-level field entirely. iOS reads only per-model.
- **(b)** Keep it but tune down to `0.3` provider-level for everything except Kimi.

(a) is cleaner; (b) is defense-in-depth in case a new model is added without a per-model value (the schema test catches that, so (b) is mostly redundant).

Doesn't fit in A3 cleanly (A3 is server-side routing config removal). Probably a tiny standalone PR (A4?) someday. Low priority.

---

## 7. **Open follow-ups still parked** (from earlier sessions, not blocking)

- **SS meeting-start gate ask** — `project_parked_followups.md` references this. No action unless SS surfaces it again.
- **Stash recovery** — old in-flight changes parked in `git stash` from the 2026-05-02 budget-gate work. Check `git stash list` periodically; drop anything obsolete.
- **`previous_tier` follow-up** — parked from CQ tier signals work. Low priority.

---

## Quick session-start checklist (for next time)

1. `git log --oneline main -10` — see what shipped while we were away
2. `gh pr list --author @me` — check open PR state
3. `git status && git stash list` — check for in-flight changes
4. Read `MEMORY.md` (always loaded), this file (`docs/next-session-punch-list.md`)
5. Run `pytest tests/ -q` once to confirm clean baseline
6. Pick a punch-list item, work it, update this doc + memory + CHANGELOG, ship

## When to rewrite this doc

When PR A2 lands on SS's side, or when A3 ships, or when the templates trial unblocks. Don't accrete old items at the bottom — keep it focused on "what's next" not "what was."
