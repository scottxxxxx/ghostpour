---
call_type: tr_response_analysis
config_slug: techrehearsal/response-analysis
served_version: 12
model_dial: sonnet-4-6 (all tiers, explicit)
recommended_model: claude-sonnet-4-6
temperature: 0.2
reconciled: 2026-07-24
---

# Response Analysis (tr_response_analysis)

## Intent

Grades a user's practice response: scorecard with rating bands, what
worked, what to fix. Shared mechanics live in the base prompt; a
`{{rating_anchors}}` slot swaps scenario-appropriate anchors per kind.

## Shaping history

- 2026-07-16: grader eval built (~/tr_eval, fixtures at 80/60/40/20
  targets). Found the interview STAR rubric was grading hard personal
  conversations (no hardConversation rubric existed); prod combo was
  the WORST orderer in the matrix (rho 0.762, MAE 16.2).
- 2026-07-16 #459: scenario-aware anchors. Interview kinds keep STAR
  verbatim; hardConversation/personal/repair/protect get calibrated
  bands plus the gap-size rule (a one-sentence fix cannot rate below
  Strong); unknown kinds fall back to STAR.
- 2026-07-17 #460: negotiation anchors (pay MAE 9.4 to 5.8, purchase
  9.8 to 6.8). Same eval PREVENTED two regressions: dedicated protect
  anchors and dedicated pitch anchors both LOST to what was shipped.
- 2026-07-17 #461: repair v2 and protect v2 anchors after Scott's
  blind-grade calibration (15 items; fixture targets recalibrated to
  his numbers). Hard-conversation clinical-tone deflator LOST on the
  bad-news beats and was not shipped.
- 2026-07-23/24 #498/#504: second-person coaching voice; spaced-hyphen
  ban.

## Known failure modes and guardrails

- Cross-scenario rubric bleed: the original sin (STAR grading a dying-
  dog conversation). Any new scenario kind needs either an anchor set
  that BEATS fallback STAR in eval, or a deliberate decision to fall
  back.
- Grade inflation from excuse-first apologies, boundaries undermined by
  unearned concessions ("a no followed by a yes is a yes"): named in
  the v2 anchors as Weak.

## Eval state

~/tr_eval: 96 fixtures, 8 beats, 7 categories; targets calibrated to
Scott's blind grading 2026-07-17. run_eval2.py uses the prod assembly
path. Rerun the affected category whenever anchors or the base prompt
change.

## Tuning rules

- Anchors are SONNET-TUNED AMPLIFIERS. On Haiku and Kimi they improve
  ordering but wreck absolute band calibration (Haiku craters, Kimi
  purchase band 83 to 25). Grader model changes require a full
  band-accuracy rerun, not a spot check.
- Repair remains the weakest category for every model; treat repair
  regressions as expected until an anchor set actually moves it.
- Fixture authorship bias is real (rubric v2 lost partly to shared
  authorship with fixtures); prefer targets calibrated against Scott's
  blind grades.
