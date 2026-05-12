# Next-session punch list — written 2026-05-12

Rolling doc. Read at the start of each session, replace at the end. The most-recent shipped state is `git log --oneline main -10` and `MEMORY.md`; this doc is *what to pick up next*, not what's already done.

## Where we are right now (2026-05-12, post-PR-#183)

Just shipped **PR #183** — added 7 per-model capability fields to `llm-providers.json` (39 models × 3 locales). Boss had decided "do PR B and then we will talk about PR A," so the cleanup pass is next on deck.

Prior context: this whole arc started from an SS audit response in which they reported that `model-capabilities.json` is ~80% dead schema (they only consume `supportsReasoning`, `reasoningLevels`, `promptReserveTokens`) and that they were guessing or hardcoding several per-model capability values. PR B added the missing fields; PR A drops the dead ones.

See:
- `MEMORY.md` → `project_2026_05_12_per_model_fields.md` (PR B record)
- `docs/handoffs/ss-per-model-fields.md` (what we already told SS about PR B)
- `docs/wire-contracts/llm-providers-fields.md` (authoritative spec for the new fields)

---

## 1. **[BLOCKER for SS reading new fields] Sync PR #183 to prod** — user-driven, not Claude

PR #183 is merged but prod still serves v9 of `llm-providers.json`. iOS won't see the new fields until we push the bundled config to the persistent store on the VM.

Three slugs need `sync-from-bundle`:
```
llm-providers
llm-providers.es
tr-llm-providers
```

Pattern (mirror of `/tmp/sync_rev3.py` from the 2026-05-11 session):
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
        "-d", '{"keys":["providers","version"]}',
        f"https://cz.shouldersurf.com/webhooks/admin/config/{slug}/sync-from-bundle",
    ])
```

Runs on the GCP VM (`35.239.227.192`, `/opt/ghostpour`). Not from this laptop — `/opt/ghostpour/.env.prod` doesn't exist locally.

**Verify after sync:** `curl -H "X-Admin-Key: $KEY" https://cz.shouldersurf.com/webhooks/admin/config/llm-providers | jq '.data.version'` → should return `10`.

This is on the boss; Claude shouldn't attempt it.

---

## 2. **PR A — `model-capabilities.json` cleanup** (the next code PR)

### Scope

Per SS audit, only three fields in `model-capabilities.json` are consumed by iOS:
- `supportsReasoning`
- `reasoningLevels`
- `promptReserveTokens`

Everything else (`provider`, `displayName`, `description`, `contextWindow`, `inputCostPerMillion`, `outputCostPerMillion`, `supportsVision`, `supportsImages`, `litellmKey`, etc.) is duplicated from `llm-providers.json` and never read on iOS.

**Goal:** strip the dead fields. Add a server-side test that pins the allowed key set so the schema doesn't drift back.

Also strip the now-redundant per-model **`supportsReasoning`** in `llm-providers.json` (PR B left it; PR A removes it because `model-capabilities.json` is the canonical source).

### Pre-work — verify SS audit before deleting

The audit response is in the prior conversation but **re-confirm with SS** before stripping fields. Easy way: search the SS iOS repo for `model-capabilities`, list every field name dereferenced, and compare to the JSON. If something we'd delete is actually consumed, the audit was incomplete and we keep it.

If we don't have SS-repo access from the GP repo, send a short Slack/email to the SS team asking them to confirm the audit by listing the exact field accessors they have. Treat anything unconfirmed as "keep."

### Files to touch

- `config/remote/model-capabilities.json` — drop dead fields per model; bump version
- `config/remote/model-capabilities.es.json` — same
- `config/remote/tr-model-capabilities.json` — same
- `config/remote/llm-providers.json` (+ `.es` / `tr-`) — drop per-model `supportsReasoning`; bump version
- `tests/test_llm_providers_per_model_fields.py` — REMOVE the `supportsReasoning` check (or move it to a separate "model-capabilities is source of truth" test)
- `tests/test_reasoning_levels_per_model.py` — should keep working (it doesn't read llm-providers `supportsReasoning`)
- NEW: `tests/test_model_capabilities_schema.py` — pin the allowed key set explicitly so the schema doesn't drift back
- `docs/wire-contracts/llm-providers-fields.md` — note that `supportsReasoning` moved out
- `docs/wire-contracts/model-capabilities-fields.md` — NEW. Document the post-cleanup canonical shape
- `CHANGELOG.md` — `### Changed` entry
- `docs/handoffs/ss-per-model-fields.md` — append a note about the cleanup once shipped, OR write a new `ss-model-capabilities-cleanup.md` handoff for SS

### Suggested test (PR A)

```python
# tests/test_model_capabilities_schema.py
ALLOWED_PER_MODEL_KEYS = {
    "supportsReasoning",
    "reasoningLevels",
    "promptReserveTokens",
    # plus anything else SS-audit-confirms they actually consume
}

@pytest.mark.parametrize("path", [
    "config/remote/model-capabilities.json",
    "config/remote/model-capabilities.es.json",
    "config/remote/tr-model-capabilities.json",
])
def test_no_dead_fields(path):
    data = json.loads(Path(path).read_text())
    for model_id, cap in data["models"].items():
        extra = set(cap) - ALLOWED_PER_MODEL_KEYS
        assert not extra, f"{path}:{model_id} has stale fields {extra}"
```

### After merge

Same prod-sync pattern as PR #183 — three slugs (`model-capabilities`, `model-capabilities.es`, `tr-model-capabilities`) plus the three `llm-providers` slugs.

---

## 3. **Templates trial — Anthropic-only BYOK request templates** (boss-gated)

### Status

Draft proposal at `docs/handoffs/ss-per-model-request-schemas-proposal.md` shipped in PR B. Boss said: "I am the boss of you and SS. I will weigh in first" — so the proposal is **not yet shared with SS**. Boss reviews; if greenlit, then SS conversation happens.

Boss also said: "3. Anthropic since taht is waht we use for SS AI" — so the trial is Anthropic-only first; expand to OpenAI/Gemini/etc. after the schema feels solid.

### Open questions to settle before any code

These are in the proposal doc but flag them here for visibility — the proposal won't get implemented until SS answers (or boss decides on SS's behalf):

1. Which providers is iOS willing to BYOK direct to? Today only OR direct. Adding Anthropic native means new key-management UI.
2. Template format — `{{slot}}` strings or richer DSL? Anthropic adaptive-thinking needs conditional substitution.
3. Slot vocabulary — canonical names for `user_message`, `system_prompt`, `images`, `reasoning_level`.
4. Image attachment shape per provider.
5. System prompt placement per provider.
6. Reasoning level integration — `model-templates.json` references the value, but does it own the picker or does `model-capabilities.json`?
7. `"default"` reasoning handling — how to express "omit this slot when source value is X."
8. Auth scheme enum (Bearer / x-api-key / URL param).
9. Response parsing — does the template encode it or stay out of scope?
10. Versioning + cache lifecycle (foreground check like protected-prompts).
11. **GP-managed path stays as-is** — templates are BYOK-only per boss. Subscription users hitting GP-managed models continue via `/v1/chat` so we keep budget gate, CQ recall, audit log, search caps server-side. Don't blur the two.
12. Failure modes — provider 4xx on stale template; iOS retry strategy.

### Suggested first PR (if greenlit)

`config/remote/model-templates.json` (+ `.es`, `tr-`) — schema sketch in the proposal doc. Anthropic models only:
- `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`

Each entry encodes endpoint, auth, headers, body template, adaptive-thinking conditional, `cache_control` injection guidance, image block shape, and the `forbidden` rules (e.g., "no temperature when thinking.type == adaptive").

iOS-side: fallback to latest cached version; baked-in build version for first-launch-no-internet (per boss direction). Wire-shape doc + SS handoff.

---

## 4. **Send the SS handoff for PR B** — user-driven

`docs/handoffs/ss-per-model-fields.md` is ready. Drop it to SS team after the prod sync (otherwise they'll fetch v9 and not see the new fields).

Key things to highlight in the message body:
- 7 new per-model fields; old iOS forward-compat (ignores them gracefully)
- `temperatureDefault: null` → **iOS MUST omit the temperature field** on Anthropic adaptive-thinking models (Opus 4.7, Sonnet 4.6). API returns 400 otherwise.
- Three open questions at the bottom of the handoff (do per-model temps match what they want; did we miss any capability fields; confirm forward-compat works).

---

## 5. **Open question for next session — provider-level `temperatureDefault` fallback**

Today every provider in `llm-providers.json` carries a provider-level `temperatureDefault: 0.7` (0.6 for Kimi). The user flagged this last session: "I'm seeing temperatures of 0.7 which seem extremely higher those even accurate for what we're using internally for shoulder surfer AI."

Now that per-model `temperatureDefault` lands in PR B, the provider-level value is only a fallback. Two options for a future PR:

- **(a)** Drop the provider-level field entirely now that every model carries its own. iOS will always have a per-model value to read.
- **(b)** Keep it but tune down: `temperatureDefault: 0.3` provider-level for everything except Kimi (which stays at instant-mode default). Matches the per-model values; serves as a safe default if a new model is added without per-model values set.

(b) is probably safer (defense in depth), but (a) is cleaner. Probably worth folding into PR A.

---

## 6. **Open follow-ups still parked** (from earlier sessions, not blocking)

- **SS meeting-start gate ask** — `project_parked_followups.md` references this. No action unless SS surfaces it again.
- **Stash recovery** — old in-flight changes parked in `git stash` from the 2026-05-02 budget-gate work. Check `git stash list` periodically; drop anything obsolete.
- **`previous_tier` follow-up** — parked from CQ tier signals work. Low priority.

---

## Quick session-start checklist (for next time)

1. `git log --oneline main -10` — see what shipped while we were away
2. `git status && git stash list` — check for in-flight changes
3. Read `MEMORY.md` (always loaded), this file (`docs/next-session-punch-list.md`)
4. Run pytest once to confirm clean baseline before changing anything
5. Pick a punch-list item, work it, update this doc + memory + CHANGELOG, ship

## When to rewrite this doc

When PR A or the templates trial ships, regenerate this whole file with the new next-step. Don't accrete old items at the bottom — keep it focused on "what's next" not "what was."
