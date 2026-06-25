# tr_research_interviewer → Haiku + web_search — change spec

Status: SPEC (not implemented). Gated on client + budget prerequisites below.
Last updated: 2026-06-17.

## Status update (2026-06-18)

Two things changed since this was written, neither of which implements the proposal below:

1. **The interviewer prompt is now GP-owned** (PR #275). Its system prompt moved from the TR client into `config/remote/tr-research-interviewer.json`, ported verbatim, and GP assembles it server-side when the client omits its own `system_prompt` (same pattern as the other TR calls, see `project_tr_prompt_migration`). The prompt content is unchanged; only its home moved. It stays a **vision** call (the LinkedIn screenshot rides in `images`), so the routing dial must stay vision-capable.

2. **The model + search switch in this doc is still NOT done.** Live today is exactly what the doc calls current state: `anthropic/claude-sonnet-4-6`, screenshot only, no web_search. The Sonnet → Haiku + web_search change remains a proposal, still gated on the same prerequisites (the search-enable path plus the TR budget mapping in `tr-budget-decision.md`).

One thing the prompt-ownership change makes cleaner: because GP now owns and assembles this call end to end, the "force `search_enabled` server-side by call_type" alternative (Prereq 1 below) is more natural to implement than it was when the prompt lived in the client. It's still blocked by the free-tier search-gate / budget-mapping issue, so revisit it only alongside the budget decision.

Everything below is the original, unchanged proposal.

## Goal

Switch the `tr_research_interviewer` call type from **Sonnet, no web search** to
**Haiku 4.5 + Anthropic `web_search`**. This is the data-backed default from the
2026-06-16 value/price test (real subject + LinkedIn screenshot): Haiku + image +
web_search returned ~90% of Sonnet's quality at ~6x lower cost and ~6x lower latency
because Haiku tokens are 3x cheaper *and* Haiku chose 2 searches vs Sonnet's 6 (less
result-bloat, fewer $0.01 search fees).

Measured ladder (interviewer/person research, same subject):

| Approach | Cost | Latency | Quality |
|---|---|---|---|
| Haiku vision-only, no web | $0.0032 | ~5s | blind to identity |
| basic sonar (text only) | $0.006 | ~5s | ~70%, can't read screenshot |
| **Haiku + image + web_search (target)** | **$0.05** | **~15s** | **~90%, one call** |
| Sonnet + image + web_search | $0.31 | ~88s | ~95% (premium/opt-in) |
| sonar-deep-research | $0.25 | ~114s | ~98% (premium/opt-in) |

Current prod state: `tr_research_interviewer` routes to `anthropic/claude-sonnet-4-6`
(model-routing.json, registered in #260) and runs **without** web_search, so it's
blind to who the interviewer actually is — it only reasons over whatever the client
pasted/sent. The target adds live web grounding for a lower cost than today's Sonnet.

## Server changes (concrete)

### 1. Flip the routing dial — `config/remote/model-routing.json`

`apps.techrehearsal.call_types.tr_research_interviewer.models` (currently lines
~139-145), all three keys Sonnet → Haiku:

```json
"tr_research_interviewer": {
  "label": "Interviewer Research",
  "models": {
    "free": "anthropic/claude-haiku-4-5",
    "paid": "anthropic/claude-haiku-4-5",
    "default": "anthropic/claude-haiku-4-5"
  }
}
```

Bump top-level `version`. This is a value change to an existing key, so prod overlay
needs a manual `POST /webhooks/admin/config/model-routing/sync-from-bundle` after the
new bundle deploys (additions auto-hydrate; value changes do not — see
reference_config_bundle_overlay_sync).

Routing only fires when the client sends `model="auto"`/`provider="auto"`; the TR
client already does this correctly (confirmed via X-TR-Build live-log marker, see
project_tr_company_research notes). No client model-pinning to undo.

### 2. Adapter — add `allowed_callers` for Haiku — `app/services/providers/anthropic.py`

The web_search tool is attached in `_build_body` (currently lines ~124-131) when
`search_enabled` is set:

```python
if request.get_meta("search_enabled"):
    body["tools"] = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }
    ]
```

**Haiku 4.5 does NOT support the default programmatic tool-calling that Sonnet uses;
the call 400s ("does not support programmatic tool calling ... set
allowed_callers=['direct']").** Add `allowed_callers: ["direct"]` for Haiku models so
the tool is direct-only. Gate it on the model so Sonnet/other call types are
unaffected:

```python
if request.get_meta("search_enabled"):
    tool = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    }
    if "haiku" in (request.model or "").lower():
        tool["allowed_callers"] = ["direct"]
    body["tools"] = [tool]
```

**OPEN QUESTION (verify before shipping):** the prod adapter pins tool version
`web_search_20250305`. The value/price test that proved Haiku+web_search used
`web_search_20260209`. Confirm Haiku 4.5 accepts `allowed_callers` on the
`20250305` version (or bump the version string). Do not ship the adapter change
unverified against the prod tool version.

## Hard prerequisites (why this isn't live yet)

1. **Client must send `search_enabled=true` on the interviewer-research call.** web_search
   only attaches when the client flag is set (chat.py search gate, ~1212). If we flip the
   dial to Haiku but the client doesn't send `search_enabled`, interviewer research runs on
   Haiku with **no web search** — strictly worse than today's Sonnet. So the dial flip and
   the client flag must land together, or the flip is a regression. **Relay item for TR.**
   - Alternative considered: force `search_enabled` server-side for this call_type to drop
     the client dependency. Rejected for now — it collides with the free-tier search gate
     (free users who send `search_enabled` get a paywall CTA and **no** LLM call, chat.py
     ~1198), so forcing it would paywall-block free TR users instead of researching. Revisit
     only alongside the budget mapping below.

2. **TR tier → GP budget mapping (the standing gap).** See `tr-budget-decision.md`. web_search
   adds a **$0.01/search separate line item** (tracked in `search_usage`, billed on top of
   token cost). TR's free/paid tiers don't map to GP budget tiers; a free TR user hitting the
   search gate gets a CTA, a "paid" TR user has no configured GP cap. This must be reconciled
   before interviewer-research-with-search is exposed to real users, or spend is uncapped.

## Sequencing

1. Verify the adapter `allowed_callers` open question (Haiku + `web_search_20250305`).
2. Land adapter change (inert until a Haiku call carries `search_enabled` — safe to ship early).
3. TR iOS ships `search_enabled=true` on interviewer research **and** the budget mapping
   lands.
4. Flip the routing dial (config) + manual sync-from-bundle. This is the go-live step;
   sequence it last so steps 1-3 are in place.

## Verification

- After dial flip + client flag: fire a real interviewer-research call from TR, confirm in
  `usage_log` that `model=claude-haiku-4-5`, `call_type=tr_research_interviewer`, and a
  `search_usage` row with `searches_performed > 0` and the $0.01/search cost. Confirm the
  returned brief is web-grounded (names real, current facts about the subject), not just a
  restatement of pasted input.
- Confirm cost lands near ~$0.05/call and latency near ~15s (vs the old Sonnet path).
- Negative check: a free TR user sending `search_enabled` still gets the paywall CTA (gate
  unchanged) — i.e. we didn't accidentally open free web search.
