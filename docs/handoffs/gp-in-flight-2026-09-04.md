# GhostPour: session handoff (2026-09-04, session cloudzap-54 [f792a1])

Same session as the 09-02 and 09-03 closes, still running. Supersedes
`gp-in-flight-2026-09-03.md`.

**Main `8ef6bdc`. Prod `e009fe4`.** The gap is two docs-only commits and is
deliberate (`docs/**` is in `paths-ignore`). Container healthy, up 14 hours.
**ZERO open PRs**, clean tree.

Six PRs merged today (#862 to #867) and two ops actions that were not PRs.

---

## ⚠ WAITING ON SCOTT (the whole list, most consequential first)

1. **Finish the analysisSchema handover.** See below. Every future sentiment
   wording change costs an App Store release until this is closed. SS is
   holding for his DIRECT word and has refused GP's relay, correctly.
2. **N-400's ruling package**: the five variables as ONE ruling, plus the
   budget reserve, plus the reply-hold threshold of 3.
3. **California purchasing vs the RESTRICT matrix**, bundled with #849.
   Two teams independently called the legal review a launch blocker.
4. **The Japanese share card**, which is a font job not a locale job.
5. The shared tier across the three apps.
6. Unwinding Tech Rehearsal spend recorded in ShoulderSurf allowances.
7. The per-app counter migration.
8. **The generic comp offer, by hand in ASC, before 2026-10-01.**

## Shipped today

**#862 `material_kind`** (prod `31f6343`), then **CLOSED by Scott**: SS will
never send it. Both GP and CQ halves stay DEPLOYED AND INERT on purpose. Do
not revert; reverting is a deploy to remove nothing.

**#864 affected-people route** carried ahead of a device reaching it.
**#865 Scott's coach mark copy**, all four locales.
**#866 the rendition docstring**, **#867 translation artifact routing**.
**#863** was the 09-03 handoff.

**Ops, not PRs:** offer-code batch 549815 loaded (pool 10 → 510) on 09-03,
and an n400 access + refresh token minted for Scott today.

## ⚠⚠ THE analysisSchema HANDOVER: DO NOT DELETE THAT KEY

GP serves `analysisSchema` in `protected-prompts{,.es,.fr,.ja}.json` and
NOBODY READS IT. GP and SS independently concluded "dead config, delete", and
**both were wrong in the same direction while agreeing with each other.** I
cut the branch and edited all four files. **Nine tests went red and stopped
it.**

`tests/test_handover_remaining_prompts.py`, 2026-08-10: *"Items 2 to 5 of
TR's handover. Scott's ruling: no prompt is the client's to own... a wording
fix is a middleware change rather than an App Store review cycle."*

It is **item 3 of an unfinished handover**. SS's half was never done. **"Zero
readers" and "unfinished handover" are indistinguishable from the reader
count**; only a written record of intent separates them, and that record was
a guard docstring neither team opened.

⚠ **This is why today cost an App Store build.** Detail in project memory
`project_sentiment_vocabulary_convergence`, including the GP-FIRST ordering
when it resumes and the three-sites trap (`sentimentScore`'s prose "+1.0
(very positive/enthusiastic)" is describing a scale, NOT a category, and a
find-and-replace eats it).

## Sentiment vocabularies: converged, and it was entirely SS's

Two sets existed: the analysis lane's TEN and the report lane's EIGHT. Scott
ruled the analysis lane converges on the eight. **It was not a GP change:**
the vocabulary is a hardcoded Swift constant
(`MeetingAnalysisEngine.swift:105`), the client assembles the prompt and GP
relays it. SS shipped `f6f778d`, plus `08dda56` for a tint map that could not
draw three of its own new values, plus `e461d06` to log the silent coercion
at `:318` that turns an unknown label into "informational".

## ⚠ The Japanese share card is a FONT job, not a locale job

SS ships ja at 100%, audited, so these are real users. GP's card serves them
English: five tables in `share_card.py` carry exactly `en, es, fr` and ja
falls back uniformly across all five.

⚠⚠ **DO NOT FIX BY ADDING `ja` STRINGS. IT WOULD RENDER TOFU.** The card is a
PIL PNG drawn with `Inter.ttf`, which has NO CJK coverage. Measured in prod:
`日本` and `京都` produce byte-identical inked-pixel counts (1168), the
`.notdef` signature. **The English fallback is protecting the card.**

⚠ **Two renderers already disagree.** The app NEVER displays GP's card; the
iMessage extension draws its own bubble locally with `UIFont.systemFont` and
full CJK. So iMessage gets correct Japanese and a shared link gets English.
Full sizing in `project_share_card_status_led`.

## N-400: on the real lane, but no device turn has arrived

**GP now owns the interview prompts and model selection** (Scott, directly,
after I refused the relay because it REVERSED his earlier instruction AND
expanded my own authority).

⚠ **Every defect attributed to the interview lane so far has been in THEIR
MOCK.** Probed against the deployed lane: the paraphrase miss, the
question-back ("what is an A-Number?" → 0 facts, answers the question,
`complete: false`), and the embedded identifier all handled correctly.
**Their simulation transcripts are MOCK evidence, not lane evidence.**

**Tokens:** access + refresh minted through `_build_auth_response` with
`app_id="n400"` so `user_apps` membership exists. Files at
`~/.gp_n400_debug_token` and `~/.gp_n400_refresh_token`, mode 600, never
printed or messaged. **Currently seeded with a 60-SECOND access token on
purpose** so the first turn exercises their refresh loop under observation.
Refresh expires 2026-10-04.

**Status: ZERO device rows.** The only n400 traffic that has ever existed is
my verification probe at 18:02:25Z. The refresh test has not run.

⚠ `/auth/refresh` is at **`/auth/refresh`, NOT `/v1/auth/refresh`**. Body
field `refresh_token`, no auth header. Rotation is unconditional and happens
BEFORE the response is written, so a crash between the 200 and the keychain
write is unrecoverable. A wrong `X-App-ID` on refresh returns 200 and
SILENTLY re-attributes the session to that app.

⚠ Scott's account has **76 active refresh tokens**, each a live 30-day
credential. Nothing is broken; nobody has ever pruned them.

## Gotchas earned today

⚠ **`gh pr checks --watch` exits 0 saying "no checks reported"** if started
before the run registers. Read the words, not the exit code.

⚠ **A log filter matching a port number.** Grepping for `401` matched
`127.0.0.1:40138`, so twenty minutes of "log activity" was my own filter.

⚠ **Case-sensitive grep missed `protectedAnalysisSchema`** when searching for
`analysisSchema`, which nearly settled the ownership question backwards.

⚠ **Reading the wrong JSON key.** `raw_request` holds `system`, not
`system_prompt`; my probe printed "len: 0" and I nearly reported the prompt
as absent.

⚠ **My own cleanup revoked the token on Scott's phone.** A blanket "revoke
everything created in the last five minutes" swept up the seeded pair.
Caught by COUNTING (`active BEFORE: 0`) rather than by assuming.

⚠ **`23FB11BA` appeared nowhere at 08:00 and had a report row by 18:00.** A
report call ran at 17:26 for the imported copy. A true finding can expire.
