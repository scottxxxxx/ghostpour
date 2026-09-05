# GhostPour: session handoff (2026-09-05, session cloudzap-6c)

Supersedes `gp-in-flight-2026-09-04.md`. Written at the end of an overnight
session that ran from 01:27Z to about 07:15Z.

**Main and prod both `203320f`.** ZERO open PRs. Eleven PRs merged and
deployed this session, #869 through #880, each read back from the
container before any partner was told it was live.

---

## ⚠ WAITING ON SCOTT (unchanged from 09-04 except the first item is CLOSED)

1. ~~Finish the analysisSchema handover~~ CLOSED on both sides (below).
2. N-400's ruling package (five variables as one ruling, budget reserve,
   reply-hold threshold of 3).
3. California purchasing vs the RESTRICT matrix, bundled with #849.
4. The Japanese share card (a font job, not a locale job).
5. The shared tier across the three apps.
6. Unwinding Tech Rehearsal spend recorded in ShoulderSurf allowances.
7. The per-app counter migration.
8. The generic comp offer, by hand in ASC, before 2026-10-01.

## Closed: the analysisSchema handover, both sides

GP #869 flipped the served `analysisSchema` to the eight in all four
locales (verbatim from SS's `f6f778d`, definitions and contrast rules
included), served 25/23/25/2 after sync. SS shipped reader `b3db82a` and
uploaded Shoulder Surf 1.17 (1618) to ASC at 23:18 CDT with it bundled.
From that build on a sentiment wording change is a config edit plus a
sync-from-bundle on four slugs, no app release.

Trap kept: the three sites per file, and `sentimentScore`'s scale prose
must not change. Guarded in `tests/test_handover_remaining_prompts.py`
by the asymmetric count (en/es/fr exactly 1 retired word, ja 0).

## Shipped: the N-400 interviewer lane, nothing to v9 in one night

Scott's ruling 2026-09-04 (relayed by the auditor session he put in
charge of the N-400 team): the app does basic intent recognition only; all
conversational intelligence lives in GP. Contract:
`N400 App/contracts/gp-interviewer-turn.md`.

`n400_interviewer_turn`, config `n400/interviewer-turn`, modeled on
`tr_counterpart_turn`: the client sends the conversation so far, known
facts and an agenda of unanswered nodes; GP decides what to ask next
(`asking`), names what the applicant did (`intent`, 14 values), speaks
first on the opening turn, and keeps the extractor lane's fact rules
verbatim. Metadata bag passes whole, so no route change. The extractor
lane `n400_interview_turn` stays deployed and mapped until the client
cuts over.

| version | PR | what |
|---|---|---|
| v1 | #870 | the lane |
| v2 | #871 | `thinking: disabled` (two of the first seven live turns hit the 2048 cap INSIDE the thinking block, one with no text, logged success) |
| v3 | #872 | `volunteer_fields`, shape rule, four reply-shape fixes from s1 |
| v4 | #873 | `deferred` response array, ten rules from the difficult applicant |
| v5 | #874 | the legal line on "should I put down X"; checkpoint object loses its summary field |
| v6 | #875 | OUTPUT FIELD ORDER: reply last, so the checkpoint decision exists before the reply is written |
| v7 | #877 | fifteen rules from both end-to-end runs |
| v8 | #879 | tuning after the v7 regression passed |
| v9 | #880 | passing-mention rule on its own utterance; interview_over one turn after the yes |

**Acceptance:** both personas passed end to end on v6 (s2-v6-full, 80
turns, 127 facts, median 3.8 s; s1-v6-full, 62 turns, 123 facts). The v7,
v8 and v9 regressions passed; reg9 was clean with 82 of 82 provenance
spans in the current utterance and zero stale cursors on 38 answer turns.
**The auditor has no open items. The remaining distance to Scott's phone
is the client cutover.** Evals under `N400 App/qa/runs/`.

**The five-run defect and its mechanism.** A checkpoint object was
returned while the reply asked the next question instead of speaking the
summary, in five runs across two contract shapes. It held only where
`section_boundary` arrived in the user message. Mechanism: the model
writes JSON in schema order and `reply` came before `section_checkpoint`,
so the reply was written before the decision existed. v6 put reply last
and it has not recurred, including at self-detected boundaries.

**Harness:** its own user (`apple_sub=harness:n400-reviewer`, tier
automation, no refresh row), 30-day access token at
`N400 App/qa/.token` (mode 600), expires 2026-10-05. Own $5 cap by
construction. Automation tier raised 20 to 60 rpm (#878) for parallel
personas. Revoke: `UPDATE users SET is_active=0 WHERE
apple_sub='harness:n400-reviewer'`.

**Three nits for v10, whenever a config ships for another reason:** "no
middle name, it's just Daniel Cho" should mint spouse_has_middle_name=no;
a disclosure after the re-asked legal question is intent `answer`; never
say "that matches your green card" of a value the lane cannot see.

## Shipped: Scott's company meeting summaries (#876)

Meeting 990BD34A: the first three minutes were garbled on the wire and the
rest clean; the stored transcript matched what every summary call
received. Haiku 4.5 declared the whole 2400-word transcript unusable
twice; Sonnet 4.6 wrote a correct report from the same text. Fix: Plus,
Pro and automation auto-summaries on Sonnet 4.6 (routing v39), and the
noisy-transcript sentence in all three summary prompts, four locales
(protected-prompts 26/24/26/3), now says to summarize the clear part and
never declare the whole transcript unusable. Both synced and read back.

## Gotchas earned tonight

⚠ **Prompt VALUE changes never hydrate on boot.** After every deploy the
container serves the previous version until sync-from-bundle runs. The
auditor's opening turn landed in that three-minute gap once and got the
old prompt. Rule for this lane: sync the moment the deploy watch returns,
send the served version before anything else.

⚠ **`gh pr view` lagged Scott's merge click by several minutes twice**;
`gh pr merge` then reported "already merged". Check the state yourself,
and when it disagrees with a relay wait one more poll before calling the
relay wrong.

⚠ **`gh run list --commit` needs the FULL sha**; the short one returns
nothing and reads as "no run".

⚠ **zsh does not word-split `$T`**: a pytest invocation with a
space-separated file list in one variable ran zero tests and the grep
filter reported nothing. Four sabotages "passed" with no test executed
before it was caught.

⚠ **Backticks inside a Python string inside a bash single-quoted
`docker exec`** are eaten by the outer shell; use bundle==file as the
instrument for served-config checks.

⚠ **`from app.config import settings` does not exist**; it is
`get_settings()`. The first harness mint died at import and wrote nothing.

⚠ **The N-400 harness sent the conversation window BACKWARDS on every run
through reg7** (current line omitted). Much of the "facts minted one turn
late" evidence was that. Their confession is in the dossier; the
evidence-turn rule stays because it was correct behaviour regardless.

## Other threads

- CQ #435 adds `name`, `project_status`, `patches_renamed`,
  `patches_archived` to the PATCH project response; read `cq_proxy.py`
  and they cross GP raw (no response model, body forwarded whole).
- N-400 client session: 432 tests green including the six refresh tests;
  four mock-lane defects fixed in their repo, not yet on the phone (build
  42). Their refresh loop's first unattended firing is 2026-09-06T01:09Z.
- Scott's N-400 device token: expires 2026-09-06T01:09Z; $1.07 of his $5
  was spent by the auditor's baselines before the harness user existed.
