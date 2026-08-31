# GhostPour: session close (2026-08-31, session cloudzap-7a [38ae2b])

Prod and main both at **1d67746** (last code deploy `d84cc1c`), healthy,
verified off the container env. **Zero open PRs.**
Supersedes `gp-in-flight-2026-08-31.md`, which is still worth reading for
the earlier half of the day.

---

## ⚠ THE ONE THING BLOCKING REAL WORK

**Scott cannot comp anyone. The offer-code pool has 10 codes and the load
of batch 549815 was DENIED BY THE AUTO-MODE CLASSIFIER.** It needs Scott's
approval on a retry, or his own hands:

    ! scp -i ~/.ssh/gcp_deploy_key <scratchpad>/load_549815.py scottguida@35.239.227.192:/tmp/ \
      && ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 \
         'sudo docker cp /tmp/load_549815.py ghostpour:/tmp/ && \
          sudo docker exec -e PYTHONPATH=/app ghostpour python /tmp/load_549815.py'

The script is gone with the scratchpad; rewrite it as: `fetch_code_values("549815")`,
refuse unless it returns exactly 500, then `offer_dispense.load_pool(offer_id=
"9142a39c-8808-4304-b414-f0bff691e94b", environment="production",
batch_id="549815", product_id="com.weirtech.shouldersurf.sub.pro.monthly")`.
Idempotent on the code PK. **Never print code strings: they are redeemable value.**

### Offer-code state, verified read-only against ASC today

    OFFER da9a627b  'Pro 50 off 3 months'    active   batch 530358 n=100  exp 2026-09-30
    OFFER bee79264  'friend-john-kirker'     INACTIVE 549830/549866/549884 expired 2026-07-27
    OFFER 9142a39c  'friend-john-kirker-v2'  active   549815 n=500, 550023 n=10, exp 2026-10-01
    OFFER 92951d61  'ops-test-scott'         active   549971 n=500       exp 2026-10-01

- **Steven is DONE.** He got and accepted his offer; his row reads `tier: pro`.
  Any older doc saying he needs an offer created is FALSE.
- **Supply is not the problem, the CLOCK is.** 1,000 codes already minted and
  unloaded. Everything unexpired dies **2026-10-01**. That is the only real
  reason to mint; Apple allows an expiry up to 6 months out. Production
  minimum is 500/batch, sandbox 10.
- **A batch cannot be re-parented.** So Scott's two rulings are two ROUNDS:
  load 549815 now under 9142a39c, and separately create a generic comp offer
  BY HAND (Apple has no API) which then forces a 500-code mint.
- **`ss_friend_steven_williams` is INERT, not leaking.** Only campaign still
  `active`, still carries a burned hardcoded code, but targets one address
  AND `tiers: ["free"]` while he is now `pro`, so it no longer matches.
  Checked, not assumed. Pause it for hygiene.
- All four `storekit_offer` campaigns hardcode a SHARED code in
  `action.value`. #836 blocks that shape on WRITE for new campaigns only.
  Comping several people wants a DISPENSABLE CTA (`offer_id` + `environment`,
  no `value`). Config write, not a deploy.

---

## Shipped this session

**#838** (`4b4523c`) moved the unknown-key passthrough property off CQ's
retired `_salience` onto a synthetic carrier `_gp_unknown_key_probe`.
Deleting the key and its assert, the obvious repair, would have deleted the
PROPERTY. Second commit is the interesting one: sabotage proved the three
named asserts were UNREACHABLE, because whole-body equality sat above them
and caught every mutation first, so their messages could never print. Fixed
by moving byte-identity last and using `.get()` (a subscript raises KeyError
before an assert can produce its message).

**#839** (`d84cc1c`) forwards `offset`, keys the cache on it, tracks CQ's
ceiling of 60.

⚠ **They were REBASED, not merged blind.** Both touched
`tests/test_woven_memory.py` and #839 was branched before #838 landed, so a
blind sequential merge could have reverted #838. Verified both coexisted
after the rebase before merging.

---

## ⚠⚠ The lesson of the session

**Forwarding a row-selecting param is HALF the fix. The other half is the
cache key.** GP's day-stable cache was keyed
`(kind, user_id, window, limit, project_id)`. Forward `offset` but leave it
out of the key and page two asks CQ correctly, CQ answers correctly, and GP
serves page one from its own cache. A 200, six correct-looking tiles, no
scroll, and **NEITHER HOP HOLDS THE EVIDENCE**: CQ's log shows a healthy
request, SS's shows a healthy response.

Proved, not reasoned: with offset on the wire but out of the key, **all four
request-side forwarding tests PASSED while the scroll was dead.** Only a test
asserting CQ was consulted a SECOND time for a second offset caught it.

Predictions written BEFORE running, all three exact: key sabotage 1 test,
wire sabotage 4, ceiling sabotage 2.

Filed as `feedback_cache_key_hides_a_forwarded_param.md`. CQ adopted the
reciprocal: **when a new param changes WHICH rows come back rather than how
they are computed, ask the middle hop whether their cache key includes it.**
**Candidate for CLAUDE.md rule 8, and that is Scott's call**, not GP's and
not CQ's, because that file is deliberately identical across three teams.

Second correction from the same exchange: GP did not REJECT `limit=30`, it
**SILENTLY CLAMPED** to 6. CQ predicted rejection, the loud failure. A cap on
the middle hop is invisible from BOTH ends, so a ceiling must be checked
against the partner's published range, never chosen alone.

---

## Inbound from SS, BOTH FULLY BLOCKED ON GP, NOT STARTED

I logged these and did not begin them. Saying so rather than hedging.

**1. `MeetingTranslation` has no `title`.** A translated meeting keeps an
English headline forever. Measured on a real device: transcript, summary and
report all render Spanish; the card headline and the `STATUS UPDATE` label do
not. The evidence is a three-element tuple:

    app/services/translations.py:32   ARTIFACTS = ("transcript", "summary", "report")

`title` was never in the contract, so `MeetingStore.overlay` had nothing to
swap. **I answered SS's ownership question: BOTH halves are GP's.**
`meeting_title.py` generates the title; `meeting_report.py` produces
`category`, which is the type label. SS takes neither and correctly refuses to
synthesise them, since a client-translated title is one nobody wrote.

**SS's framing, worth keeping as a general lesson:** *a control that works
invisibly is indistinguishable from one that does not work.* Three of four
visible fields swapping is WORSE than zero, because zero reads as "not
implemented" and three reads as "broken". Scott tapped "Show in Spanish"
repeatedly and concluded the button was broken. Same family as the fallback
that erases "asked and got nothing" versus "never asked".

**2. A share carries no sender language.** Scott deliberately chose Spanish;
the recipient was still offered "Translate to Spanish, the original is kept".
His words: "Why would she ever want to put it back to English after I went
through the effort of specifying it should be in Spanish."

Needs a sender-intent field distinct from the spoken `transcript_language`.
**Three states must be distinguishable: sender chose X, sender chose nothing,
sender's choice unknown because the bundle predates the field.** The third
rules out a plain nullable string, since that conflates the last two. I did
NOT name the field rather than name it badly; SS is waiting on the name before
writing the import path.

---

## Cross-team state

**CQ** is live with paging: `limit` 1..60, `offset`, and `total_available` /
`offset` / `has_more` on the HOME digest only. **CQ RULED `offset` does NOT
apply to the seam route** (`/meetings/{origin_id}/woven`): capture order has
nothing to page, and a knob that does nothing is worse than an absent one
because somebody eventually sets it and reasons from the result. **Leave the
seam forwarding NO query params.**

CQ also stopped serving tiles with no headline (selection refuses; loss counted
as `no_headline_written` in `dropped`), which dropped `total_available` from
322 to 265 and it will climb as a recovery backfill runs. Headline backfill is
FINISHED at 3,839. GP needs no change: nothing pins those fields and the deploy
restart emptied the in-process cache, so the move is already fully visible.

### ✅ CLOSED same session: the `total_available` collision was RENAMED

Was: two sibling routes, one name, opposite counting rules, ~8x apart for
the same user on the same day, with GP rendering one into user-visible copy.
**CQ ruled and shipped it as `tiles_available` on the woven route (CQ prod
`4564cf9`), before SS wrote their decoder.** There is no collision left to
document, so do NOT add one to GP's dossier docs.

    /v1/quilt/{user_id}         total_available   pre-cap denominator   2136
    /v1/quilt/{user_id}/woven   tiles_available   post-prune            265+

Both counting rules were correct for their own route and neither changed:
pre-cap has to be the real denominator because GP renders it as a FLOOR in
user copy, and post-prune has to exclude tiles that will never appear
because "showing 6 of N" is a promise about a scroll. The NAME was the only
defect, so the name is what changed.

**Nothing needed from GP**: forwards verbatim, no test pins it, no code
reads it (the `total_available` hits in `context_quilt.py`, `chat.py` and
the contract-lane tests are all the DOSSIER lane and are untouched).

⚠ `tiles_available` will CLIMB over the next hours. That is expected, not
drift: CQ's recovery pass is writing the headlines that the new
no-headline-no-tile gate currently suppresses.

**Also additive, notification only:** person detail gained `reconciling`
(null when nothing pending). Passes through GP by construction.

---

## Waiting on data or on a session

- **#837 pre-flight timing: still ZERO turns.** Needs Scott to open the app.
  Instrument proven with a positive control (435 of 561 log lines matched), so
  the zero is real absence, not a broken grep. ⚠ Container logs only retain
  since the last restart, so `--since 24h` cannot speak to earlier.
- **Recital detector: still zero** on both `cq_recital_checked` and
  `cq_recital_detected`. CQ holds a join until one real event lands.
- **The `limit=30` end-to-end check and SS's prod echo are the SAME blocker.**
  GP verified the deploy by CALLING the functions in the prod container
  (`_woven_limit(30)` returns 30, `(600)` returns 60, `("x")` returns 6;
  `_woven_offset` gives 12 / 0 / 0). **That is an execution check, NOT the
  wire.** Auth, handler, outbound query and thirty tiles back are unproven,
  and a defect between the handler and CQ survives all of it.
  **GP cannot close it: it needs a session and minting one is outside standing
  authority.** Scott opening the app once pays for all three at once.

## Also waiting on Scott

- **Limit ceiling**: matched CQ's 60, which moves the bounded cache
  (`_MAX_ENTRIES = 2048`) from ~8.5 MB to ~88 MB worst case at CQ's ~713
  bytes/tile. CQ notes nothing requests 60 today. Scott can ask for lower.
- Older, unchanged: `minute_mark` (a turn INDEX at extraction, not a reversal
  of doc 21), recall scoping posture #827 (re-price: the tire store was SHORT
  at 2,303 chars, not project-less), test-account revocation (`ddc3df33`),
  `enforce_admins`, the 300-char research-notes cap, the 1.16 upload.
