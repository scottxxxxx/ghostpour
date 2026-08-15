# Artifact generation: what we built, what we prove, and what happens below tier

Status: plan agreed 2026-08-15. Code on `feat/structured-workbook-render`
(PR #683), entirely inert. Nothing imports it and no lane changes
behavior until the wiring described here is done deliberately.

## What this is

The model returns a structured plan and **we** render the xlsx, instead
of handing the whole job to the provider's code execution sandbox.

Correction worth stating once, because the whole design depends on it:
the provider's file lane does **not** hand us structured output we could
intercept. It runs Python in a sandbox and streams finished bytes.
Asking for a plan and asking for a file are two different requests, not
two views of one thing.

## Measured, not assumed

Same meeting, same ask, only the mechanism changed:

| | provider file lane | contract + our renderer |
|---|---|---|
| scenarios | 24 | 42 |
| content in the file | 15,103 chars | 31,300 chars |
| cost | $0.2673 | $0.1772 |
| house style defects | 4 | 0 |

Nine contracts exist: test plan, action register, decision log, risk
register, open questions, requirements, option comparison, budget,
cross-meeting topic tracker. All nine were run against real transcripts
from stored meetings, except option comparison, for which a scan of all
123 stored transcripts found zero meetings that evaluate named
alternatives against criteria, so it used a synthetic one and its
extraction fidelity is the least verified of the nine.

That absence says nothing about demand. The corpus is one user in one
job role. Which artifacts matter is answerable only from usage once
users are generating them, which is why `artifact_type` telemetry is a
blocking item below and not a nice to have.

## Model choice

**Sonnet 4.6**, not Sonnet 5. Under contract, 4.6 writes 202 characters
of expected behavior per row against Sonnet 5's 95, fills the notes
column on 42 of 42 rows against 15 of 46, and costs about the same.
Sonnet 5 is newer and cheaper per token and is the worse choice here.
Never rank these on row count: Sonnet 5 produced more rows and less
substance.

Opus 5 produces the best content (115 scenarios, perfect fill) but takes
488 seconds and $1.13, and needs more than 32k output tokens, so it is
out for an interactive surface.

## Intent recognition

Hybrid, and a small model was already in the stack:

1. `looks_like_file_ask`, free regex prefilter
2. `classify_generation_intent`, **Haiku 4.5** at temp 0, fail open,
   returns `{file_request, format, gist}`
3. artifact type, which was purely lexical and was the weak link

Measured on 108 user asks generated blind to our hint vocabulary:

| | acceptable | no match | wrong |
|---|---|---|---|
| lexical hints only | **21%** | 73% | 6% |
| + Haiku artifact classifier | 100% (tuning set) | 0 | 0 |
| + classifier, **held out set** | **98%** | 2% | **0** |

False positives measured separately at **zero**, on both ordinary
meeting questions and file asks outside the nine.

Classification costs **$0.0005 per ask**. It is the same Haiku call we
already make, extended, so no new dependency and no added latency.

## Blocking items before wiring

1. **Nothing has run through the real chat path.** Every result above
   comes from a standalone harness. The production path has
   `_question_portion`, the confirmation envelope, the reply
   interpreter, streaming and metering, and a contract has touched none
   of it.
2. **Entitlement and caps.** `generation_gate` enforces tier and a per
   tier generation cap. Contracts must ride the same gate or a free user
   generates without limit.
3. **Metering.** The contract call must land in `usage_log` carrying
   `artifact_type`, or spend is invisible and the only honest answer to
   which artifacts people use is lost for as long as it is missing.
4. **Duration honesty.** `expected_seconds` is surfaced to users as
   literal text and defaults to 150. Measured contract times run 22s to
   187s. Each contract needs its own measured value.

Build during wiring rather than prove first: a failure path that falls
back to the provider lane when a render raises or schema retries are
exhausted, and `asyncio.to_thread` around the render, since 72ms of
synchronous CPU on `--workers 1` stalls every concurrent request.

Known and not blocking: the topic tracker must not be offered against
single meeting context, where it returns structurally perfect and
substantively empty output; and the delivery shape must match what
`collect_generated_files` hands the client.

## Below-tier users: detect for everyone, then upsell

**Scott's ruling 2026-08-15: there is no gate at the plan level on
DETECTION.** Every user at every tier gets file intent recognised, gets
the disambiguating question when we are unsure, and only then, once we
know they want a file we can build, meets the tier boundary. The
boundary is an upsell moment, not a silent dead end.

### What exists today

A below tier upsell already exists (Scott 2026-07-14):
`generation_tier_shortfall` returns the served `min_tier` when the ONLY
thing failing is the subscription tier, and a served line is prepended
to the reply with `{tier}` resolved at request time, plus a coherence
instruction so the model does not contradict it.

### Four gaps between that and what we want

1. **Below tier turns never reach the classifier.** The code says so
   outright: detection there is "the deterministic layer only". We now
   know that layer is **21% accurate** on natural phrasing, so the
   upsell misses roughly four of every five people it exists for. This
   was a sound cost decision when the classifier's price was unmeasured.
   It is now **$0.0005 per ask**: a thousand free users asking twice a
   day is about a dollar a day, against a Pro subscription at $14.99 a
   month. Reverse it.
2. **It is off.** `_UPSELL_DEFAULTS` ships `enabled: False`.
3. **The copy is one generic line**, "If you were a {tier} subscriber, I
   could generate a Word or Excel file for you." It names a capability
   and sells nothing. It should expand what Pro includes, file
   generation and everything else, and it should be able to name the
   artifact the user just asked for, since "I could build you that risk
   register" converts better than "I could generate a file".
4. **It is not editable from the dashboard.** There are zero references
   to `upsell` in `admin.html`. It is reachable only through
   `PUT /admin/entitlements/documents`.

### Target flow, all tiers

    file ask detected (regex prefilter, then Haiku)
      -> not a file request        : normal chat, teaser CTA if vocabulary present
      -> file request, unsure which: ONE disambiguating question, tier irrelevant
      -> file request, known artifact
           -> tier allows          : offer, confirm, build
           -> tier below min_tier  : upsell copy naming THAT artifact,
                                     plus what else the tier includes

Disambiguation happens **before** the tier check on purpose. A user who
is asked "did you want the risk register or the action items" and
answers has told us what to upsell them on, and has seen the product
understand them before it asks for money.

### Copy requirements

Served config, locale aware, dashboard editable, with:
- a headline benefit line that expands the tier's value beyond files
- an `{artifact}` placeholder for what they just asked for, falling back
  cleanly when we did not resolve one
- the existing `{tier}` placeholder, which already resolves from served
  config so an availability move needs no code change
- house rule applies: no em or en dashes in any of it

Per `feedback_no_limitation_framing_in_copy`, this states what the
system does and what the tier adds. It is not an apology for a
limitation.

## Sequencing

Wire behind a flag with ONE contract. Prove the round trip end to end
including gate, metering, duration and delivery. Then enable the rest.
The risky part is plumbing you can watch, not nine artifacts at once.
