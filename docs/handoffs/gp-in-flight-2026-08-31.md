# GhostPour: what is in flight (written 2026-08-31, session cloudzap-35 [6823a2])

Prod and main both at **d84cc1c**, healthy, verified off the container env.
**Zero open PRs.** Supersedes `gp-in-flight-2026-08-30.md`.

⚠ Later than the body below: #838 and #839 are MERGED AND DEPLOYED (18:04
UTC). Anywhere further down that says 3bf465c or calls those PRs open is
superseded by this line.

---

## ⚠ TOP ITEM (REWRITTEN 2026-08-31, session cloudzap-7a): offer codes.

**Scott corrected the premise: Steven ALREADY GOT AND ACCEPTED his offer.**
Everything the previous version of this section said about needing a
`friend-steven-williams` offer is now FALSE and is replaced here rather than
left standing. What Scott wants to mint is for OTHER people he wants to
comp, not for Steven.

**Verified in prod today, read-only against App Store Connect:**

    OFFER da9a627b  'Pro 50 off 3 months'    active   batch 530358 n=100  exp 2026-09-30
    OFFER bee79264  'friend-john-kirker'     INACTIVE batches 549830/549866/549884 (expired 2026-07-27)
    OFFER 9142a39c  'friend-john-kirker-v2'  active   batch 549815 n=500, 550023 n=10, both exp 2026-10-01
    OFFER 92951d61  'ops-test-scott'         active   batch 549971 n=500  exp 2026-10-01

**Supply is NOT the problem. 1,000 free-3-month codes are already minted and
UNLOADED** (549815 n=500 and 549971 n=500). The dispense pool holds only the
10 from 550023, `available: 10`, nothing reserved or dispensed.

**The constraint is the CLOCK, not the count.** Every unexpired batch dies
**2026-10-01**, 31 days out. That is the only real reason to mint: Apple
allows an expiry up to 6 months out. Production minimum is still 500/batch
(live-probed 2026-07-27); sandbox accepts 10.

**A minted batch is permanently bound to the offer it was minted against.**
It cannot be re-parented. So Scott's two rulings today are two ROUNDS, not
one:

1. **Now:** load batch 549815 into the pool under its true offer 9142a39c.
   Zero minting, zero new entitlements, 500 codes available immediately.
   ⚠ **NOT DONE: the load was BLOCKED by the auto-mode classifier.** It
   needs Scott's approval or his own hands. Idempotent (`INSERT OR IGNORE`
   on the code PK), so re-running is safe.
2. **Then:** Scott creates a generic comp offer (e.g. `friend-comp-3mo`) BY
   HAND in App Store Connect. Apple has NO API for offer creation. Only then
   can GP mint 500 against it and load. This necessarily creates 500 new
   entitlements, which is why it is a separate decision from step 1.

**`ss_friend_steven_williams` is INERT, not leaking.** It is the only
campaign still `active` and it still carries a hardcoded one-time-use code
that Steven has now burned. Blast radius was CHECKED, not assumed: it
targets one address (`dr54zs2js7@privaterelay.appleid.com`) AND
`tiers: ["free"]`, and his row now reads `tier: pro`, so it no longer
matches. Worth pausing for hygiene; nothing is exposed.

**All four storekit_offer campaigns hardcode a SHARED code in
`action.value`** (`ss_native_test_scott`, `ss_friend_john_kirker`,
`ss_friend_john_kirker_preview` paused; `ss_friend_steven_williams` active).
That is the shape #836 now blocks on WRITE for new campaigns. Comping
several people wants a DISPENSABLE CTA carrying `offer_id` + `environment`
with NO `value`, so each person draws their own code from the pool. Config
write, not a deploy.

---

## Shipped and deployed today

    #825 recital guard          #833 people description-dismiss routes
    #826 field-name inventory   #834 turn_id billed-vs-not
    #827 recall scoping doc     #835 Woven digest endpoints
    #828 refusal template       #836 offer-code shared-code guard
    #829 cohort retraction      #837 chat pre-flight timing
    #830 injection gate+detector #736 eval harness
    #831 redeem copy (en/es/fr/ja)  #819 TR handoff doc
    #832 detector logs both sides

**#825 was an INCOMPLETE fix and #828 is the real one.** Replaying the real
incident against the deployed prompt: control 17/48 recited, #825 alone
5/48, #828 0/48. A prohibition leaves the model to invent a replacement and
the replacement is to explain itself, which means naming what it was told
not to name.

---

## Waiting on Scott

1. **Offer codes (see the rewritten TOP ITEM).** Steven is DONE. Two
   things are open: approve the load of batch 549815 (blocked by the
   auto-mode classifier, creates nothing), and decide whether to create
   a generic comp offer by hand, which forces a 500-code mint.
2. **Woven headline backfill.** CQ's lane writes forward only, so the first
   real digest will be almost entirely `headline: null` and tiles render
   `fact`. It is a WRITE, so it needs his go. SS will explain it so a thin
   quilt is not read as a broken ranking.
3. **`minute_mark`.** CQ reframed it: the ask is to stamp a turn INDEX at
   extraction (no transcript content), NOT to reverse doc 21. Much smaller
   decision than it first looked.
4. **Recall scoping posture (#827).** Option C lost its main justification
   when CQ measured that the tire store was SHORT (2,303 chars), not
   project-less. Re-price rather than carry forward.
5. Older, unchanged: test-account revocation (`ddc3df33`), `enforce_admins`
   on branch protection, the 300-char research-notes cap, the 1.16 upload.

## Waiting on data, not people

- **Pre-flight timing (#837) is live and has recorded ZERO turns**, because
  there has been no chat traffic since the restart. It answers "which phase
  owns the 5.32s" the moment Scott uses the app. **Do not restructure the
  chat path until it has data**; the restructure is a 2,800-line reorder of
  the hottest path and three separate plans were overturned by measurement
  today.
- **The recital detector logs BOTH sides** (`cq_recital_checked` /
  `cq_recital_detected`). CQ is holding a join until one real event lands.

## Cross-team state

- **CQ**: half deployed (`e26b0f4`, `7731fb7`, `242eaf7`). Woven endpoints
  at `/v1/quilt/{user_id}/woven` and `/…/meetings/{origin_id}/woven`.
- **SS**: Woven client shipped DARK at `7d54a8c`, decoder fixes at
  `c091239`/`ef3e7ed`, `_freshness` at `4bdf2d1`. They delete their local
  fallback only when they see real tiles on Scott's phone.
- **Owed by GP**: the prod echo. SS runs an authenticated call with a wrong
  `project_id` from the device and sends the raw body + `request_id`; GP
  reads the request ring for that same id. Both halves of ONE call.
  **GP cannot do this alone: it needs a session, and minting one is outside
  standing authority.**

## Lessons filed (see the memory dir)

- `feedback_instruments_examine_representations.md` now carries **four
  failure families**, all found today: instruments that render a CLEAN
  result; instruments examining a REPRESENTATION (a type that was a Union,
  text that was a program); **two correct decisions composing wrong** (CQ
  made `window` lenient, GP's `limit: int` 422'd it first); and **a verified
  HALF reported as a settled WHOLE** (I generalised from one route to a
  prefix without printing the prefix; GP declares 14 routes there).
- The producer-side rule, in SS's wording: **when you add a state to a field
  that already has states, name the ERASING IDIOM in the consumer's language
  and ask whether they handle it.** SS's `decodeIfPresent(…) ?? []` silently
  erased CQ's null-means-could-not-compute.
- ⚠ **Sabotage-revert trap hit twice more today (4th and 5th).** Reading the
  note does not work. The only mechanism is: **commit BEFORE sabotaging, as
  one motion.**

---

## Late addition: SS ran the device half, token expired

SS ran the probe on Scott's iPad (build 1400). GP's side, confirmed in the
container log:

    15:24:47.040  GET /v1/memory/woven?window=7d&limit=6                     401  3ms
    15:24:47.099  GET /v1/memory/woven?…&project_id=not-a-real-project-id    401  2ms

**The query string arrived intact**, which SS's 401 could not prove from
their end (auth refuses identically with params stripped). 2-3ms means auth
rejected before any CQ call, so nothing reached CQ and nothing was cached.

⚠ **A 401 leaves NOTHING in GP's request ring** (it records inbound bodies,
after auth). I had offered the ring as the matching half; it only carries
the call once the token is valid.

**Still owed: the wrong-`project_id` BODY**, i.e. `project_known: false`
versus the key being absent. The iPad's stored token is expired. Cheapest
path is to open the app so the ordinary refresh runs, then re-run the probe.
**GP cannot do this half alone** — it needs a session, and minting one is
outside standing authority.

Two SS client defects found on the way, both of which would have made this
live route look dark:

- `WovenDigestClient` read keychain account `cloudzap_jwt`; everything else
  uses `cloudzap_api_key`. The fetch returned nil BEFORE requesting, so a
  signed-in device falls back forever, indistinguishable from a 404. SS
  would have reported the route dark after a good deploy, confidently.
- Their probe's first run wrote an EMPTY FILE: the no-credential path
  `continue`d past the write, so "could not run" looked like "ran and found
  nothing" — under a comment that said a probe recording only success is not
  an instrument. Second comment-versus-code divergence in two days.

**The generalisation (SS's, worth keeping):** a FALLBACK erases the
difference between "asked and got nothing" and "never asked". The silent
fallback that makes shipping dark safe is the same mechanism that makes a
dark ship indistinguishable from a broken client.

---

## ⚠ CORRECTION to two things above

**1. The headline backfill is FINISHED** (CQ confirmed 3,839 patches carry a
written headline, 2026-08-31). It was never pending Scott, and the earlier
"first contact will be almost all nulls" warning has now expired entirely.
The original correction, kept because the sequence is the receipt: CQ reports
1,475 of 4,637 processed, 1,125 written, 76% accepted; the validator refuses
~1 in 4 and has caught invented numbers. So "the first digest will be almost
entirely null headlines" has a shelf life of minutes, not days.

⚠ CQ earlier told GP this backfill "needs Scott's go because it writes", and
GP relayed that to Scott as pending his approval. It is now running. **Not
GP's call to police** (CQ owns their repo and has standing authority to
direct GP's work), and it may well have been approved earlier, but the
statement GP gave Scott is now false and is corrected here rather than left
standing.

**2. `_salience` is being PULLED from the wire** (CQ #364), not
grandfathered. GP needs no change: it forwards whatever arrives. But
`tests/test_woven_memory.py` fixtures still carry `_salience`, so they will
drift from the real shape once #364 deploys. The byte-identity test STILL
PASSES and still proves its property (GP returns what CQ sent), because it
asserts against the mock rather than live CQ. Update the fixture when
convenient; it is staleness, not breakage.

**Why it was still on the wire is a receipt worth keeping.** CQ had a test
named `test_salience_is_computed_but_kept_off_the_public_shape`, with a
comment saying GP had declined to take the field, **asserting that the field
IS PRESENT**. Name and comment described the intent; the assertion enforced
its opposite; it was green throughout and nobody opened it *because the name
already said the reassuring thing*.

Only GP's vantage point could see it: GP forwards the body verbatim so it
sees what TRAVELS, CQ sees what it believes it sends, SS sees what it
decodes and was told to ignore. Invisible from both ends, obvious from the
middle. Rule 5 with the sign flipped: usually the middle hop is where a
defect hides, here it was the only place it was visible.

---

## Late addition 2: CQ paging, and a cache key that would have eaten the fix

CQ moved the woven route while this session was open. **PR #839** (CI
running at handoff time; #838 is GREEN and also open).

    limit    was 1..6, now 1..60   default still 6
    offset   NEW, >= 0, default 0
    total_available / offset / has_more   NEW response fields, HOME ONLY

**Two defects on GP's hop, both quiet:**

1. `offset` was not a parameter at all, so every page returned page one.
2. `_woven_limit` did `max(1, min(6, n))`. CQ predicted a client asking for
   30 would be REJECTED here. It was not: it was **SILENTLY CLAMPED** to 6,
   a 200 carrying the wrong page size. Rejection is the loud failure;
   clamping is the quiet one, and it is the same family as everything else
   on this feature: an observable indistinguishable from success. A cap on
   the middle hop is invisible from BOTH ends, so a ceiling has to be
   checked against the partner's published range, never chosen alone.

**⚠ The half that would have shipped broken.** Forwarding `offset` does NOT
fix the scroll on its own. GP's day-stable cache was keyed on
`(kind, user_id, window, limit, project_id)`. With offset forwarded but not
KEYED: page two asks CQ correctly, CQ answers page two correctly, and GP
serves page one from its own cache. A 200, six correct-looking tiles, no
scroll, and NEITHER hop holds the evidence, because CQ's log shows a healthy
request and SS's shows a healthy response.

Proved rather than reasoned: with offset on the wire but out of the key,
**all four request-side forwarding tests PASSED** while the scroll was dead.
Only a test asserting CQ was consulted a SECOND time for a second offset
caught it. Predictions written BEFORE running, and all three landed exactly:
key sabotage 1 test, wire sabotage 4, ceiling sabotage 2.

Filed as `feedback_cache_key_hides_a_forwarded_param.md`. CQ adopted the
reciprocal and will now ask by default: **when a new param changes WHICH
rows come back rather than how they are computed, ask the middle hop whether
their cache key includes it.** Candidate for a CLAUDE.md rule 8; it cost
real debugging today, which is the bar that file sets.

**Needing no GP change, confirmed not assumed:** the three new response
fields pass through by construction (`{**body, "_freshness": ...}`, no
response model); the tile ARRANGEMENT change (types interleave rather than
following rank) does not move GP fixtures, which assert against a mock, not
live CQ; the seam route's "GP must not reorder" test is unaffected.

**CQ RULED: `offset` does NOT apply to the seam route.** One meeting in
capture order has nothing to page or arrange, and it serves none of the
three new fields. Adding the param at GP's hop would have created a knob
that does nothing, which is worse than an absent one because somebody
eventually sets it and reasons from the result. **Leave the seam forwarding
NO query params.**

**Cache memory, recorded before someone finds it at 2am.** At CQ's ~713
bytes/tile a 60-tile entry is ~42 KB against ~4 KB for six, so the bounded
cache (`_MAX_ENTRIES = 2048`) moves from ~8.5 MB worst case to ~88 MB.
Bounded either way. CQ notes the ceiling is unlikely in practice: `limit`
still defaults to 6 and SS has no window switcher or infinite scroll yet, so
nothing requests 60 today. Scott has been told and can ask for a lower cap.

---

## Close of session cloudzap-7a: both PRs merged and live

Prod **d84cc1c**, healthy, zero open PRs. Scott gave the merge go.

**#838** unknown-key passthrough moved off CQ's retired `_salience` onto a
synthetic carrier, plus the assert reorder that made the named asserts
reachable at all. **#839** forwards `offset`, keys the cache on it, and
tracks CQ's ceiling of 60.

⚠ **They were rebased, not merged blind.** Both touched
`tests/test_woven_memory.py` and #839 was branched BEFORE #838 landed, so
#839 still carried the old `_salience` fixture. Merging in sequence without
checking could have reverted #838. Verified after the rebase that both
changes coexist (probe present, no stale `_salience`, offset on the wire and
in the key, ceiling 60), 41 local, CI green again on the rebased branch.

**Verified deployed by EXECUTION, not by reading source**, because CQ's
point was that a route table is not proof. Called inside the prod container:

    _woven_limit(30) -> 30    _woven_limit(600) -> 60    _woven_limit("x") -> 6
    _woven_offset("12") -> 12  ("-5") -> 0  ("banana") -> 0

**What that does NOT cover, stated so nobody upgrades it later:** the wire
path end to end. Auth, handler, outbound query, thirty tiles back. A defect
between the handler and CQ survives everything above. The real check needs
ONE authenticated call at `limit=30`, and **GP cannot make it: it needs a
session and minting one is outside standing authority.** Same blocker as the
prod echo. Scott opening the app once pays for all three at once (CQ's
confirmation off the container log, #837's first turns, most of SS's echo).

**Deploy side effects, both good:** the restart emptied the in-process woven
cache, so CQ's `total_available` 322 to 265 move is fully visible with no
day-stale window; and the cache key changed shape anyway, so every prior key
would have missed regardless.

## Still open at close

1. ⚠ **Batch 549815 load STILL BLOCKED by the classifier.** Pool at 10.
   This is the only item blocking real work: Scott cannot comp anyone.
2. **#837 pre-flight timing: still ZERO turns.** Needs Scott to open the app.
3. **Generic comp offer**: optional, manual ASC, forces a 500 mint.
4. **Limit ceiling**: matched CQ's 60. Moves the bounded cache from ~8.5 MB
   to ~88 MB worst case. Scott can ask for lower; CQ notes nothing requests
   60 today.
5. **CLAUDE.md rule 8 candidate**: the cache-key rule. Three-team file, so
   Scott's call, not GP's and not CQ's.
6. **⚠ `total_available` COLLISION, awaiting CQ.** `/v1/quilt/{user_id}` is
   pre-cap ("the real denominator", Scott = 2136); `/v1/quilt/{user_id}/woven`
   is post-prune (Scott = 265). Sibling routes, one name, opposite counting,
   ~8x apart. GP renders the first into user-visible copy. Only GP sees this
   because only GP holds both routes, same as `_salience`. SS builds the
   expand against it THIS WEEK, so before their decoder exists is the cheap
   moment. If CQ keeps both names, write the collision into GP's dossier docs.

