# GhostPour: session handoff (2026-09-02, session cloudzap-01 [ee41f7])

Prod and main both at **f61ee26**, deployed and healthy, verified by
EXECUTION in the container rather than off workflow status.
**ZERO open PRs.** Eight merged and deployed today (#849 to #856).
Supersedes `gp-in-flight-2026-09-01.md`.

---

## ⚠ STILL BLOCKING, UNCHANGED FOR THREE SESSIONS

**Scott cannot comp anyone.** Offer-code pool is at 10; the load of batch
549815 was DENIED by the auto-mode classifier and never retried. Needs
Scott's approval or his own hands. Recipe is in
`gp-in-flight-2026-08-31-close.md`, still correct. Untouched today.

## Accept-Language: MERGED AND DEPLOYED (#858, prod `9a27d96`)

This section said UNMERGED at the time it was written. It shipped later the
same session; corrected in place, and the branch it named is deleted.

GP's `_cq_proxy` BUILT its outbound headers rather than copying them and did
not take the `request` object at all, so `Accept-Language` never reached CQ.
Their per-locale people strings (#406, prod 750a4b1) were therefore INERT
for every proxied caller: every user got English no matter what the client
sent, and because CQ's headerless output is byte-identical to their old
output IT LOOKED LIKE THE FEATURE WORKING FROM BOTH ENDS. Neither side's
tests could see it. CQ's prove their writer, ours proved our RESPONSE
passthrough, and this was a REQUEST-side hole (rule 3). CQ found it by
ASKING what the proxy does with the header.

Fixed with a NAMED ALLOWLIST (`_FORWARDED_REQUEST_HEADERS`), which CQ
independently proposed too: the next header either side wants asks the same
question, and an allowlist with a request-side test per entry is the shape
rule 3 rewards. `request` is KEYWORD-ONLY AND REQUIRED across all 41 call
sites; auth headers apply last so a client cannot override them.

**Verified in the container on the OUTBOUND call**: forwarded verbatim with
q-weights intact, nothing outside the allowlist crosses, a client's own
Authorization does not reach CQ, and a headerless caller still sends
nothing.

⚠ **THAT PROVES OUR HOP, NOT A DEVICE.** The agreed acceptance test for #406
is a localized string on a phone, which needs an SS build. All three teams
hold that framing: a device or nothing, because the failure mode is CQ
correctly returning English to a headerless request.

⚠ **CQ CLAIMED THREE FEATURES BLOCKED ON THIS HOP. ONLY ONE DID**, checked
against the code rather than accepted. `refresh_headline` (#407) is a BODY
field already crossing via that route's `extra="allow"` (a scar from the
`patch_type` silent no-op), and `days_present_*` are RESPONSE fields already
proved on real bytes by the two-read diff. Both wait on an SS client build,
not on GP. Worth keeping because the reverse error is expensive: two teams
parking work behind a hop that was never in their way.

Also from Scott via CQ: the refresh affordance lives in the patch detail
overflow menu, one patch at a time, so no background write arrives there.

## Waiting on Scott (nothing is blocked on GP)

1. **THE TIER IS STILL SHARED.** The largest remaining instance of his own
   multitenancy ruling. A Plus subscription bought in ShoulderSurf makes
   that person Plus in Tech Rehearsal and N-400, because Apple issues
   subscriptions per developer TEAM and `tier` lives on the account row.
   Per-app matrices decide what "plus" MEANS for an app, never whether this
   user IS plus. Closing it is per-app entitlement on top of a team-wide
   purchase: a revenue decision, not a leak.
2. **Unwinding TR's recorded spend.** TR came off the shared meter today,
   forward-only. Spend already sitting in people's ShoulderSurf allowances
   is untouched. Rewrites history, so it is his call.
3. **Policy matrix legal review.** He took ownership 09-01. Four questions
   for the reviewer are in #849's description; `recommend_answer` is the
   one worth his lawyer's time.
4. **Per-app counter migration.** #856 GUARDS the two shared counters; it
   does not separate them. Real separation needs a table and a backfill,
   and the two use different period models.
5. **N-400's $5 budget** is DONE (#850). No longer open.

---

## Shipped and deployed today

- **#849** policy engine, State x Form x Capability, fail-closed.
- **#850** per-app flat spend cap ($5 n400) + `record_cost` takes app_id.
- **#851** dashboard multi-app filter, options DERIVED from the registry.
- **#852** `n400_interview_turn` prompt config, dossier, dial, plus the
  jurisdiction variant axis and fail-closed prompt variables.
- **#853** CQ people passthrough tests, list and detail.
- **#854** served-copy fix: web search + Spanish idle tips.
- **#855** multitenancy: TR off the shared meter, entitlement fail-open
  closed, entitlements resolved PER APP across 20 call sites.
- **#856** shared account counters refuse to charge a non-owning app.

**Config pushed to the prod overlay** (value changes do NOT hydrate, these
were deliberate PUTs): tiers v59 en / v58 es,fr,ja; feature-highlights v8
x4; idle-tips.es v8. Survived the deploy restart, re-verified after.

---

## ⚠⚠ THE FINDINGS THAT MATTER MOST

### 1. A fix can land on the surface nobody calls

The web-search contradiction was corrected on `/v1/config/tiers` and
reported done. ShoulderSurf QA then found `/v1/tiers` still serving the
retired copy. MEASURED at the edge: **`/v1/tiers` 248 requests in the
window, `/v1/config/tiers` 1**, and that one was their check.

Cause: the two endpoints read DIFFERENT KEYS. `/v1/config/tiers` serves the
remote config verbatim (`search`); `/v1/tiers` iterates `features.yml`
NAMES, preferring a remote entry of the same name (`web_search`), else the
YAML copy. The lookup always missed.

**Both reported version 58 while disagreeing**, because `/v1/tiers` echoes
the display config's version while sourcing feature copy elsewhere. A
matching version across the two proves nothing. See
`reference_two_tier_surfaces.md`.

### 2. Reimplementing a rule that already exists

`may_charge_shared_counter` compared raw app-id strings. A missing header
arrives as `None` at some call sites and as the LITERAL `"unknown"` at
others; `resolve_app_dir` has always normalised both. The string compare
refused `"unknown"` and broke quota accounting for every user whose build
sends no app id. Two real tests caught it. It now defers to
`resolve_app_dir` on both sides.

Same shape hit twice more today: `usage_tracker.py` and `tr_budget.py` each
had NO module logger while I added `logger.` calls to them.

### 3. Three tests that could not fail, all mine, all found by sabotage

- **Route ordering**: asserted the forwarded path ended in `/network` with
  four segments. Disabling the literal route so the catch-all took it left
  ALL SIXTEEN TESTS GREEN, because both handlers build the identical path
  when entity_id is the string "network". cq_proxy's own docstring says so
  one line above the code. Rewritten with two instruments that fail for
  DIFFERENT reasons: the query (network forwards none, detail forwards
  `request.url.query`) and asking the router which endpoint owns the path.
- **The generation guard**: grepped chat.py for the guard's variable names
  near the write. `if False:` left every string in place and stayed green.
  It asserted the guard had been TYPED, not that it RAN. Extracting the
  predicate is what made it testable.
- **A fixture that was fiction**: the people-row test fixture invented
  `person_id`, `display_name` and top-level counts. None exist; it is
  `entity_id`, `name`, and both spellings live inside `signals`. The
  passthrough property held either way, which is why a green suite could
  not catch it: a test that forwards an arbitrary dict cannot tell you the
  dict is fiction. Found only by reading a real prod response.

---

## Cross-team state

**CQ** (`contextquilt-9d`). Two batches of added fields shipped (#395
`signals.days_present_*`, `cadence.days_observed`; #397 `presence.
days_present`, `days_since_last_statement`, `max_days_not_raised`). GP's
passthrough is proved on real bytes for BOTH routes: list and detail read
twice on Scott's account, direct-from-CQ vs through-GP, identical with only
`server_time` differing, positive control confirms the comparison can fail.
They also retired the `ghostpour` storage identity: 2,671 ACL rows re-owned
to ShoulderSurf, zero collisions.

⚠ **A relayed authorisation is not an authorisation.** CQ relayed "Scott
says yes" for a prod read; GP declined and asked Scott directly. He then
authorised it himself. Keep that boundary.

**N-400** (`per-prompt-llm-routing`, renamed from `n400helper-05`). Their
client speaks the interview envelope to spec, 167 tests. Turns ride
`POST /v1/chat` with `call_type` in metadata, NOT a `/v1/n400/*` route;
they had assumed one and it would have 404'd. `system_prompt` must be
ABSENT or GP skips assembly silently and serves an unguarded model.
⚠ **The lane 422s until they send the eight required metadata keys.**

**SS Social** (`shouldersurfsocial-d0`) found both copy defects and then
found that the first fix had missed the endpoint that matters. Verified
both fixes live from their side.

---

## Method notes worth keeping

- **Measure which surface is used before fixing one.** The edge access log
  answered in one query what an hour of reasoning would not have.
- **A latent leak gives no signal on the day it stops being latent.** Both
  shared counters were ShoulderSurf-only in practice; that is the argument
  FOR the guard, not against it.
- **Refusing to charge is the safe direction** when sharing is the bug.
- **A JSON round-trip reformats.** An edit through `json.dump` turned a
  32-line change into 377 by moving 1-space indent to 2, hiding the real
  edit inside whitespace. Raw text substitution with a count assertion.
- **Word boundaries, not substrings.** `reunion` is inside `reuniones`;
  `sesion` inside `sesiones`. Cost two false positives in one day, one in a
  test that failed on the CORRECT file.
- **Do not run two suites at once.** An overlapping pair produced one
  spurious integration error; confirmed by re-running alone, not assumed.
- **`tail` on a background command's output** means the traceback you will
  want later was thrown away at capture time.
