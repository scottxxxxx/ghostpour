# GhostPour: what is in flight (written 2026-08-31, session cloudzap-35 [6823a2])

Prod and main both at **3bf465c**, healthy, verified on `/health`.
**Zero open PRs.** Supersedes `gp-in-flight-2026-08-30.md`.

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

**1. The headline backfill is RUNNING, not pending Scott.** CQ reports
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
