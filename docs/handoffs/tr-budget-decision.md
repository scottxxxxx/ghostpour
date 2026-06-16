# Tech Rehearsal budget reconciliation — decision input

Status: OPEN. Blocks merge of PR #251 (company research) and #253 (interviewer analysis).
Last updated: 2026-06-16.

## The problem

Tech Rehearsal (`com.weirtech.techrehearsal`, X-App-ID `techrehearsal`) now routes its
LLM calls through GhostPour (the cloudzap→ghostpour provider rename shipped in #254). But
TR has its own free/paid tier model that does **not** map onto GP's budget tiers. Today a
TR account inherits whatever GP budget tier its user row carries — e.g. Scott's account is
`pro` with `monthly_cost_limit_usd = -1.0` (unlimited). That means TR spend is currently
**uncapped at the gateway**, governed only by TR's own client-side gating, which GP can't
see or enforce. Before the interview-prep features ship to real TR users we need an explicit
mapping: TR tier → GP budget tier (and the per-call ceilings that implies), so spend can't
run away.

## What the call types actually cost (live telemetry, 2026-06-16)

Pulled from `usage_log` for the TR account. Only two TR call types have run against prod so far:

| call_type | model (prod) | avg cost/call | avg out tok | avg latency |
|---|---|---|---|---|
| `tr_parse_jd` | claude-haiku-4-5 | ~$0.016 | ~2,918 | ~28s |
| `tr_research_interviewer` | claude-haiku-4-5 | ~$0.0032 | ~329 | ~5s |

Not yet exercised in prod (no rows): `tr_parse_resume`, `tr_mock_interview`,
`tr_response_analysis`, `query`. Company research (PR #251) runs on Perplexity `sonar`
out-of-band, ~$0.005/call (grounds + cites; the deep-research-via-OR path is broken — see
[[project_tr_company_research]]).

## The Sonnet decision for `tr_parse_jd` (new data point)

`tr-jd-analysis.json` carries `recommendedModel: claude-sonnet-4-6`, but `model-routing.json`
(`apps.techrehearsal`) routes `tr_parse_jd` to Haiku 4.5 — a silent downgrade from what the
prompt author intended. A/B run 2026-06-16 (same prompt, same ~4k-char JD, max_tokens 4096):

| | Haiku 4.5 (prod now) | Sonnet 4.6 (config's pick) |
|---|---|---|
| Latency | ~25s | ~58s (2.3x) |
| Cost/call | ~$0.016 | ~$0.051 (3.2x) |

Quality (judged by Opus 4.8): mechanical extraction (title/company/salary/level) is a tie —
Haiku is fine there. The inferential fields that are the feature's reason to exist are
**markedly better on Sonnet**: `implicitBar` correctly reads the role as staff-adjacent
(Haiku gave a generic "proven engineer"); `interviewerLens` becomes an actual probing
question a coach can use rather than an abstract description; `redFlagsForCandidate` reads
subtext (on-call ≈ 8–9 rotations/yr, pace-vs-correctness tension) Haiku misses. Conclusion:
restoring Sonnet is a real quality win on the differentiated work, and it matches the prompt
author's recommendation.

Cost consequence to fold into the budget math: moving `tr_parse_jd` to Sonnet roughly
**triples** the per-parse cost ($0.016 → $0.051). At plausible TR free-tier volume this is the
single biggest line item in the interview-prep flow. The budget tier mapping needs to either
(a) price Sonnet JD parses into the paid tier and keep free-tier on Haiku, or (b) cap free-tier
JD-parse count per period, or (c) accept the spend if free-tier volume stays low. This is the
decision, not a foregone conclusion.

Latency consequence (not budget, but coupled): Sonnet's ~58s makes the TR client-timeout fix
mandatory and larger — the iOS JD-parse timeout must be ≥120s (or stream), not the ~12s it is
today. See the relay note handed to TR 2026-06-16.

## Recommendation (for Scott's call)

1. Define TR tier → GP budget tier mapping before merging #251/#253. Minimum: free vs paid,
   each with a `monthly_cost_limit_usd` and per-call-type model routing.
2. Route `tr_parse_jd` to Sonnet for paid; keep Haiku for free (or cap free JD parses) —
   this is the cheapest way to capture the quality win without uncapping free-tier spend.
3. Leave `tr_research_interviewer` on Haiku (cheap, fast, quality adequate at 329 out tok).
4. Hold the company-research sonar path as-is (~$0.005, works); do not wire deep-research-via-OR.

## Open / depends on

- TR app must send a signal GP can map to a budget tier (or GP infers from the TR account's
  subscription). Today GP only sees the X-App-ID, not the TR-side entitlement.
- Wire-up of #251/#253 in the TR app is still pending regardless of this decision.
