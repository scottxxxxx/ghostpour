# Cost and Limits — decision log

The living record of every cost-control decision: what's limited, why,
what it costs us in practice, and what was deliberately left unlimited.
Append-only: new decisions get a dated entry, reversals get a new entry
pointing at the one they reverse. When a "should we cap X" conversation
starts, it starts HERE, against the evidence, not from scratch.

## Unit economics baseline

- Credits are cost-denominated: 10,000 credits = $1.00 of provider
  spend (`CREDITS_PER_DOLLAR`, budget_gate.py).
- Revenue net of Apple's 15%: Plus $8.49/mo, Pro $12.74/mo.
- Free allocation: $1.75/mo (17.5K credits), marketed as ~5 hours.
- Paid tiers are cost-UNCAPPED by design (monthly_cost_limit -1);
  containment for paid users comes from per-feature limiters below
  plus routing (cheap lanes for cheap work), not a hard budget wall.

## Metered surfaces and their limiters (as of 2026-07-25)

| Surface | Limiter | Observed cost (30d prod) |
|---|---|---|
| Chat/summaries/analysis | model routing per call_type × tier; free budget gate | summary avg $0.0065; analysis avg $0.018 |
| Web search | 75/mo Plus, 120/mo Pro (soft warn at 80), 0 Free; ~$0.01/search + the carrying turn | search-capable turns avg $0.01 to $0.05 |
| Images | count per tier (1/3/5 per question) + server downscale 1568px/0.8 JPEG (#474) | folded into turn cost |
| File generation | Pro-only; QUIET 100 builds/mo (generations_per_month, 2026-07-19); extraction max_tokens 8K/12K | template extraction avg $0.022, max $0.026 |
| Document attachments | SIZE only: 2 files, 25 MB each, 600 PDF pages, 22 MB total. NO count/cost limiter | doc-heavy turns $0.87 to $2.70 EACH (see 2026-07-25 entry) |
| Reports | one per meeting, regen gated by transcript retention (30d) | avg $0.068, max $0.16 |
| TR calls | per-call_type routing dials; managed prompts | tr_mock avg $0.045; graders $0.012 to $0.030 |

## Decision log

### 2026-05-10 — Budget gate + credits (PRs #109 to #121, #166 to #173)
Free tier gets a hard monthly cost allocation enforced server-side;
credits (10K/$) become the client-facing unit so vendor pricing never
shows. Paid tiers deliberately uncapped on cost. Allocation counter
resets on real tier transitions (fresh allocation on state change);
usage_log is the never-resetting ledger for dashboards.

### 2026-07-10 — Per-tier image economics (#474)
GP dictates downscale + JPEG quality per tier (1568px/0.8). Image cost
control is preprocessing, not a separate meter. BYOK excluded.

### 2026-07-1x — Search caps (search-caps.md)
75/mo Plus, 120/mo Pro, soft threshold 80 on Pro, 0 Free. Search costs
~$0.01/call plus the carrying turn. CTAs served from tiers config.

### 2026-07-19 — Generation monthly count cap
Pro 100 builds/mo, QUIET (no counter UI; lane goes dormant at cap and
file asks fall back to inline answers). Rationale: bounds the sandbox
lane's worst case without advertising a limit nobody should hit.

### 2026-07-25 — File generation: NO new limit (this review)
Scott's question: should file generation carry a cost limit like
search? Evidence says no. The template lane costs $0.022/build average
and $0.026 max (extraction is max_tokens-bounded); even a user
saturating the 100-build cap costs ~$2.50/mo against $12.74 net
revenue. Double-bounded already (count cap × output cap). Revisit only
if the sandbox lane (pptx/pdf builds) shows a materially different
profile in prod data.

### 2026-07-25 — Document attachments: the real exposure, OPEN
Scott's question: should a 25 MB attachment in context carry a limit?
The 30d data says this is the one unbounded-cost lane: the six
costliest requests on record are all doc-attachment chat turns, $0.87
to $2.70 each (up to ~544K tokens of cache write on a 1.7M-token
cached context). Mitigations already in place: prompt caching makes
repeat turns on the same docs ~10% of the first turn; size caps bound
the per-turn ceiling; Pro-only gates the audience. Worst case math: a
user who starts ~5 fresh max-size doc sessions per month costs more
than Pro's net revenue; a determined abuser could 10x that.

Agreed direction (Scott + assistant, 2026-07-25): two steps.
1. NOW: ops-side whale alert, not a user-facing cap — alert when any
   user's month-to-date provider cost crosses a threshold (proposed
   $10, ~80% of Pro net revenue), through the existing alert
   transport. Catches the exposure silently, no product change, no
   limitation copy.
2. POST-LAUNCH: decide a user-facing attachment allowance (count of
   doc-carrying turns per month, search-style) only with real
   distribution data. Do not pre-build limits for behavior we have
   never observed in a paying stranger.

## Open questions

- Whale-alert threshold value and whether it also fires per-day.
- Whether sandbox generation builds (pptx/pdf) cost enough to deserve
  their own line in the table (collect data as Pro users generate).
- BYOK carve-outs: all limiters here assume managed routing; BYOK
  users burn their own keys and bypass cost caps by construction.
