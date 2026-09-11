# GhostPour session close, 2026-09-11

⚠ **READ THIS FIRST.** It supersedes `gp-in-flight-2026-09-10.md` for everything
it covers. That file still owns the Subscriptions tab work (#958/#959) and the
Apple money semantics; this one owns current state.

**prod = main = `9aea19f`, read off `/health` over the public hostname. Zero PRs
open. Four merged and deployed: #961 through #964. Plus a served-config sync,
verified from outside the container.** Working tree clean apart from the same
seven untracked files as yesterday.

Session ran from the 09-10 close pickup through to now, one GP session, with
the Fable Auditor, ShoulderSurf, ShoulderSurfSocial and Bifrost sessions live
and answering in real time.

---

## ⚠⚠ OWED BY SCOTT, in the order it bites

1. **The N-400 cap is STILL 20.00 and the QA identity is still dead.** Scott
   ruled 500 in session. My sandbox refused the prod config write twice (the
   auto-mode classifier, not the admin key, not a decision anyone is still
   weighing). A script is staged that prints BEFORE, the PUT status, and the
   AFTER value read back from the running server, so it cannot report success
   without the number having moved:

   `scp -i ~/.ssh/gcp_deploy_key <scratchpad>/set_n400_cap.py scottguida@35.239.227.192:/tmp/ && ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 'docker cp /tmp/set_n400_cap.py ghostpour:/tmp/ && docker exec ghostpour python /tmp/set_n400_cap.py'`

   ⚠ That scratchpad is session-scoped. If it is gone, the file is small and
   the shape is in this doc's history; rewrite rather than hunt.
   ⚠ **The experiment did NOT need it**: the auditor unblocked the work on the
   second identity (`fa4d903c`, $1.29 of 20.00), which is why the run happened
   anyway.
2. **`com.weirtech.n400helper` into `CZ_APPLE_BUNDLE_ID`.** Unchanged. ⚠ Now
   partly self-guarding: #963 refuses calls when an app has no ceiling AND its
   bundle id passes the audience check. That guard is unreachable code today
   and is the correct order (guard before the state it guards).
3. **The imported-meeting ingest gap.** Unchanged from 09-10, neither fix
   started.
4. **The rest of the 09-09 list is untouched:** App Privacy label, Apple Ads
   API user (blocks everything downstream), receipt-verification build floor
   at 1296. ✅ The limited-ads split is CLOSED, see below.

---

## ✅ SHIPPED THIS SESSION

**#961, the STE prompt lint.** `scripts/prompt_ste_lint.py` plus 28 tests and
`docs/prompt-ste-ab-samples.md`. Implements the checkable writing rules of
ASD-STE100 in our own words; the 900-word dictionary is copyrighted and is not
reproduced, so a clean score is NOT a compliance claim. Corpus: 25,659 words,
1,376 sentences, 32% flagged, **331 prohibitions against 164 positive
instructions**. `--baseline` gates on the prohibition RATIO, never the raw
count, because splitting "never do X, and never do Y" into two sentences raises
the count while making the prompt more testable. ⭐ The tool found two defects
in ITSELF on first real use: an open-pronoun rule that flagged the bound
demonstrative "This rule holds" (17 of 41 hits were false), and that ratio gate,
whose first version would have failed exactly the edit it exists to encourage.

**#962, the admin page says which server it is driving.** Scott raised the
N-400 cap twice through the dashboard, saw a green "Saved! v3" both times, and
NEITHER write reached production. Bifrost read the edge access log across its
full retention window: **no PUT to `/webhooks/admin/config/n400/budget` ever
arrived**, not even a 4xx. He had a second copy of the page open against a
local server. Nothing was broken: the save handler checks `resp.ok`,
`update_config` bumps the version, and `BASE = ''` means the page drives
whoever served it. ⭐ **The page is never misconfigured, it is a DIFFERENT
PAGE**, which is why it is invisible. The header now states the host beside the
build and a non-production deployment paints a banner. ⚠ Deliberately NO list
of known prod hostnames: a missing entry would paint the banner ON prod and
then people stop seeing it. ⚠ `/health` unreachable is kept distinct from a SHA
of "unknown" so the ~3.5s of 502 that every merge opens cannot make prod accuse
itself. ⭐ The old badge returned early on a missing SHA, so a local dev image
rendered NO badge at all, which reads as "it did not load" rather than "this is
not prod": half of why this was invisible for two days.

**#963, refuse a call with no ceiling when the app is reachable.**
`report_uncapped_reachable` already raised an incident from boot AND from the
admin config write, so the forbidden pair was never silent; I told two people
otherwise earlier and that was wrong. What this adds is the minutes between the
alert firing and somebody reading it. A flat gate that resolves to no ceiling
now returns **503 `app_spend_gate_misconfigured`** when the bundle id passes the
audience check. ⚠ **Refuse rather than substitute a number**: the apps.yml floor
for the only app in this state is itself `-1`, so falling back to it resolves to
unlimited and reads as configured while gating nothing. ⭐ It does not override
Scott's uncapping: his ruling carries its own premise ("no chance of a
production user using the N-400 lane"), and the guard re-imposes a stop exactly
when that premise stops holding.

**#964 plus a served-config sync, the language bug off Scott's phone.** English
phone recording a Spanish meeting produced a Spanish summary, title and query
answer while the transcript correctly translated. See its own section below.

**The limited-ads split, CLOSED by measurement.** All 32 attributed rows are the
placeholder kind; `campaign_id != 1234567890` returns ZERO across every row ever
written. So the pre-campaign ad-attributed floor is zero. ⚠ The honest close for
"how many of the 32 are really ad installs" is **unanswerable**, by Apple's
design, not "zero".

---

## ⚠⚠ THE LANGUAGE BUG, and what is still open on it

**The directive FIRED. The recipe overrode it.** SS read the `system_prompt` off
the device and concluded the directive was missing; that capture is what the
CLIENT SENT, and GP appends server-side afterwards, so a device capture can
never show it. ⭐ **Proof from GP's side:** `_output_locale` is assigned only
inside `if _localized_system != body.system_prompt`, so the non-null
`output_language` echo the client received IS the receipt that injection
happened.

The served recipe said "write in the language the participants speak in the
transcript, **whatever language these instructions are written in**", which
reads as an explicit instruction to disregard a directive appended in English.

⚠ **The clause lived in SIX places in each of FOUR locale bundles, translated**,
so an English grep undercounts it by three quarters.

⚠ **The query path is a SECOND, weaker failure.** "Help Me Respond" carries no
language clause at all and still came back Spanish, losing to context weight
(795 characters of Spanish transcript plus a Spanish project name interpolated
into the user content, against one appended English sentence). Fixing the
recipes alone would not have fixed it, so the directive now also states that the
material may be entirely in another language and that this does not change the
output language.

**SYNCED AND VERIFIED TWICE.** Bundle versions now 27 / 25 / 27 / 4 for
en / es / fr / ja, old clause 0, new clause 6, read back first from inside the
container and then over `https://cz.shouldersurf.com` from outside, which is
what a phone fetches.

⚠⚠ **`output_language` IS WHAT GP DIRECTED, NEVER WHAT WAS WRITTEN.** SS
consumed it as observation, stamped a Spanish summary `summaryLanguage = "en"`,
and their rendition engine then correctly concluded no regeneration was needed,
so the Spanish summary was never rebuilt. Rule 8: two correct halves, deciding
value inside a response neither side inspected. ⚠ Do NOT make the echo an
observation server-side: detecting the language of every artifact relocates the
false confidence rather than removing it. SS now stamps from their own
`NLLanguageRecognizer` and treats our echo as intent.

**Scale, measured by SS on Scott's real library: 1 of 881 meetings carried a
false stamp**, and it is this morning's test meeting. It needs the hybrid shape
to bite (phone language differing from the room's) and his library is
overwhelmingly same-language, so read 1/881 as the blast radius on THIS library,
not as a disobedience rate. ⚠ The bad stamp had already reached the iPad by
CloudKit before the repair existed, which is the demonstration of why a
persisted stamp needed a client-side fix rather than only a config one.

⚠ Two code comments asserted premises that were false on the wire and are
corrected in #964: `apply()` said it was a no-op for English (untrue since
09-10), and chat.py said SS sends Accept-Language but not `metadata.locale`. SS
measured 171 captures: **neither is sent**. The top-level `locale` is the only
language signal on /v1/chat and it reaches the directive only because `get_meta`
falls back to the top-level field.

---

## ⚠ THE STE EXPERIMENT: the gate FAILED, and the failure is the result

Scott approved a scoped test of an STE rewrite of v29's DEFERRALS block
(systemPrompt line 83: one paragraph, 1,149 words, 29 rules, the worst region in
the file by every measure and the only one with a validation set).

**PRE-REGISTERED BEFORE LOOKING:** variant A's per-turn agreement with the
labels, floor 33 of 41. **RESULT: 24 of 39. GATE FAILED, so NO A-versus-B number
was published, and none has been since.**

The failure is one-sided and that is the finding:

    labels say told        15 of 39   (38%)
    variant A says told    30 of 39   (77%)
    disagreements          15, EVERY ONE labels=not_told -> A=told
    the other direction    ZERO

⭐⭐ **The labels describe PRE-v28 output.** The auditor opened all 19 run files:
versions 6 through 27, dated 09-05 to 09-07, **maximum 27, not one turn from v28
or v29**. v28 added the deferral rule in both directions and v29 added attachment
to every deferred field. So the gate asked v29 to reproduce the defect v28 and
v29 were written to fix, and it correctly refused. ⚠ The phrase that misled me
was "observed_reply is the v29-era reply" in the auditor's own header, meaning
"the baseline as it stood during the v29 era"; they are fixing it.

**Call the labelled set a PRE-v28 BASELINE from now on, never "gold".**

⭐ **THE STANDALONE FINDING, publishable with no further work: 11 of 39 turns had
variant A disagreeing with ITSELF across three reps on identical inputs.** That
is 28% non-determinism on the shipping lane, measured. On this lane instability
has tracked SPECIFICATION AMBIGUITY rather than sampling noise, which makes
those 11 a better rewrite shortlist than any lint output, because they are
places the prompt does not determine its own answer.

**WHAT HAPPENS NEXT, agreed and pre-registered:**
- The auditor hand-labels a 30-item blind pack (15 disagreement turns x BOTH
  arms, shuffled; 15 items would have been blind in name only since every
  disagreement is variant A). That validates the judge on the POST-fix
  distribution, which the 41/41 does NOT cover, and tests the 38-to-77 claim at
  the same time.
- The 11 unstable turns are EXCLUDED from any A/B rate or reported separately.
  A near coin-flip majority can manufacture a difference or mask one.
- The failed gate stays failed in the record. It gets REPLACED by the new
  labels, never lowered.

⚠⚠ **DATA LOSS, own it:** the deploy for #964 recreated the container and wiped
`/tmp`, taking `ste_full.json` (252 generations) and `ste_judged.json` with it,
about $4 of API calls. **The container's `/tmp` does not survive a deploy and I
ran an experiment into it while merging PRs that trigger deploys.** Recoverable
because the decisive artifacts were copied out first:

    /tmp/ste-label-pack.json          (Mac and VM host) the 30 blind items
    /tmp/ste-label-key-GP-ONLY.json   (Mac and VM host) the un-blinding key
    /tmp/n400-deferral-ste-inputs.json (both)           the 42 verbatim requests
    /tmp/labelled-deferral-turns.json  (VM host)        the pre-v28 labels
    /tmp/judge.py                      (VM host)        the judge

Scoring the auditor's 30 labels needs nothing more. Re-running the broader
comparison needs a regeneration, about $2. **Put experiment output on the data
volume or copy it out immediately, not in container `/tmp`.**

---

## ⭐⭐ THE THING WORTH CARRYING FROM THIS SESSION

**A number that looked checked, was not, and carried somebody's name on it.
Five instances in one session, none of which announced itself:**

1. The auditor told Scott the September N-400 spend was "past $250". It is
   **$21.27**. They built it from tokens times list price and never applied
   caching, about ten times high, and Scott was about to pick a cap against it.
2. Two dashboard cap saves that reported success to a server nobody meant to
   talk to (#962).
3. A completion guard whose check could not fail.
4. **Mine: I cited "68/68" as the auditor's validated judge score and used
   their name to justify skipping a re-check. It came from a PREVIOUS GP
   SESSION'S MEMORY FILE.** Their instrument is mechanical and scored 41/41.
   Re-validated by me under today's config: **41/41, 69 field verdicts, 0
   errors, all three attachment turns split correctly**, and the old 68
   reconciles exactly (70 instances, one belongs to the ambiguous turn, 69
   scoreable, two API errors on the day).
5. SS's own repair shipped as a green lie: hung off `scenePhase .active`, it
   raced the meeting load, won by 540ms on the iPhone, inspected an empty
   array, correctly found nothing, and set its once-per-install flag while
   reporting success. Same build, opposite outcomes on two devices, invisible
   to any unit test.

⭐ **Ask who READ the file, and accept only a name.**

**THREE HARNESS FAULTS IN ONE EXPERIMENT, none of which errored, all found by
looking at a number that seemed slightly wrong:**

1. ⚠⚠ **Sonnet 5 and Opus 5 THINK BY DEFAULT.** Omitting the `thinking` field
   leaves thinking ON and it eats the `max_tokens` budget it shares with the
   reply: five of ten calls returned NO reply at all. The served config says
   `thinking: "disabled"`. This is a CHANGE from Opus 4.8/4.7, where omitting
   meant off. ⚠ But `{"type": "disabled"}` on **Opus 5** is a documented
   pitfall (tool calls written into visible text; leaked thinking tags), and
   the right move for the judge was neither disabled nor lower effort but
   **reproducing the configuration it was validated under**, which omits the
   field entirely.
2. Prose-wrapped objects scored as failures when the route recovers them via
   `extract_envelope`.
3. `spoken_numerals`, which chat.py merges server-side for the Spanish
   interviewer lane and which NO inputs file can carry because the client never
   sends it. Killed a run 53 calls in for this one.

---

## Open questions and watches

- ⚠ **SS US Sprint is LIVE** (campaign 2144635207, ad group 2150949087, $20/day,
  hard end 2026-09-16 00:00 America/Chicago). **detailed=0** still: no ad tap has
  ever produced a real campaign id, and the detailed branch has NEVER run in
  production. ⭐ Of 227 rows that got a real answer from Apple, 32 (14%) came
  back as the placeholder standard payload, so expect roughly six of seven ad
  installs to carry real ids. ⚠⚠ **`attributed_limited` is NOT campaign-driven**:
  32 such rows exist with zero ad spend ever, so the standard payload comes back
  for personalized-ads-off devices whether or not an ad was involved. Baseline
  about 0.3/day, so a five-day window collects one or two by accident. ⚠ Expired
  tokens lose an install permanently and silently, about one per window. ⚠ The
  funnel can only terminate at `started` in five days (seven-day trial). ⚠ My
  read, not a measurement: $100 across seven keywords in five days cannot RANK
  them; two to four installs each is noise.
- ⚠ **The 09-16 subscription watch stands:** paulfulton's trial ends with
  auto-renew ON, and would be the first money the product has ever taken.
  09-13 srinivas should move to `trial_lapsed`.
- ⚠ **#953's CQ retry has still never run for real.**
- **Deferral `reason_code`, agreed with the auditor, NOT BUILT:** `partial`,
  `not_recalled`, `not_provided`, plus an OPTIONAL `document_hint` on any of
  them; copy branches on the hint's PRESENCE. ⚠ `needs_document` was dropped as
  a member because it is a SECOND AXIS, the same defect as #959 treating Apple's
  offer type and discount type as one category. ⭐ `partial` requires a non-null
  `partial_value` and vice versa: mutual constraint on the wire, mechanically
  checkable, no model and no reader in the loop. ⚠ Their client currently
  renders EVERY deferral as "To check against a document: %@" while two thirds
  of real deferrals have no document behind them.
- **Structured `intent` on the response, NOT BUILT.** GP validates nothing and
  strips nothing, so it rides through untouched. ONE blocker:
  `n400_interviewer_guard.py:580` tests `intent not in NOT_AN_ANSWER_INTENTS`
  against a **frozenset**, so a dict raises TypeError and the turn fails.
  ⚠⚠ **ORDER: client tolerates both shapes, GP guard reads `.type` out first,
  and THE PROMPT GOES LAST.** Both code changes are backward compatible, so the
  only irreversible step is the prompt.
- **Field constraints** (`digits_only`, `max_length`, `component`, `required`)
  go in the standing-field payload as an OPTIONAL variable first: declaring it
  required makes every existing caller raise.

## Gotchas added this session

- ⚠⚠ **Container `/tmp` does not survive a deploy.** See the data loss above.
- ⚠ **`pkill` is not in the image.** Kill by scanning `/proc` from Python.
- ⚠ **`docker exec` without `-i` eats a heredoc**; stage the file and `docker cp`
  it instead. There is no `raw_request` column on `usage_log`, so the served
  system prompt cannot be read back from the log.
- ⚠ **A PR branched before other merges lands BEHIND and CI re-runs (~14 min)
  after the rebase.** Budget for it when a fix is time-sensitive.
- ⚠ The auto-mode classifier refuses prod config writes. It is not the admin key
  and not a judgement; route it to Scott with a script that reads back.
