# GhostPour: what is in flight (written 2026-08-31, session cloudzap-35 [6823a2])

Prod and main both at **3bf465c**, healthy, verified on `/health`.
**Zero open PRs.** Supersedes `gp-in-flight-2026-08-30.md`.

---

## ⚠ TOP ITEM: offer codes. Scott said "mint and load"; minting is NOT what is needed.

**The pool is no longer empty.** Loaded batch `550023` (10 codes) into
`offer_id=9142a39c-8808-4304-b414-f0bff691e94b` (`friend-john-kirker-v2`),
production. `available: 10`. Idempotency proven by running it twice
(`loaded 0, skipped 10`).

**I did not mint, and minting would have been wrong.** Discovered read-only
against App Store Connect:

    OFFER da9a627b…  'Pro 50 off 3 months'    active   batch 530358 n=100
    OFFER bee79264…  'friend-john-kirker'     INACTIVE batches 549830/549866/549884
    OFFER 9142a39c…  'friend-john-kirker-v2'  active   batches 549815 n=500, 550023 n=10
    OFFER 92951d61…  'ops-test-scott'         active   batch 549971 n=500

- **There is NO Steven Williams offer.** That is the whole root cause: his
  card served a code from JOHN's offer because he never had one of his own.
- Live unexpired codes already exist (500 + 10, expiring 2026-10-01), so
  minting would have created 500 MORE real 3-month-free entitlements for
  nothing. **Apple's production minimum is 500 per batch** (live-probed
  2026-07-27; sandbox accepts 10). "One offer per person, batch of 10" is
  sandbox sizing and cannot be done in production.

**What Scott must do by hand, and only he can:** create a
`friend-steven-williams` offer in App Store Connect. Apple has **no API** to
create an offer, only to mint codes against one. Then:

1. `POST /webhooks/admin/offer-codes/mint` (min 500 in production)
2. `POST /webhooks/admin/offer-codes/load-pool` with the returned `batch_id`
3. Rewrite the CTA to carry `offer_id` + `environment` and DROP its
   hardcoded `value`
4. `GET /webhooks/admin/offer-codes/pool-status` is the exhaustion gauge

**Until then `ss_friend_steven_williams` is ACTIVE and still serving a
hardcoded shared code.** #836 stops NEW campaigns doing this accidentally
(a `storekit_offer` CTA must be dispensable or say `shared_code: true`), but
it validates on WRITE, so the existing four are untouched and still live.

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

1. **The Steven offer** (above). Manual ASC work, nobody else can.
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
