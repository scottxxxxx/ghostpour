# TR → GP: the mock-path rollup evidence was an artifact of the misrouted lane (2026-08-28)

Short version: thank you for the routing fix. We re-ran the measurement that
motivated the `overall`-as-a-function-of-per-question-scores prompt change, and
on the corrected lane **our half of the evidence does not reproduce**. Your
half does. We think that changes the scope of the change from both modes to
one, and we would rather say so before you ship it than after.

## What we re-ran

Same fixture, same protocol as 2026-08-19: eight runs of `tr_response_analysis`
/ `InterviewScorecard` through the harness credential, byte-identical input,
`tools/gp-harness --only score`.

```
overall, 2026-08-28 (routing corrected)   42, 42, 42, 43, 43, 44, 44, 44   spread 2
overall, 2026-08-19 (misrouted harness)   55, 55, 55, 55, 57, 62, 63, 63   spread 8
```

Per-question came back 0.95 / 0.35 / 0.0 on seven of the eight runs (one said
0.30), and the four rating labels were identical in all eight, same as before.

The residual, `overall` minus 100x the mean of the per-question scores that the
same call wrote:

```
2026-08-28   -1.3 to +2.3   (spread 3.7)
your live path, 10 runs   -0.4 to +8.0   (spread 8.4)
```

## Why this matters for the prompt change

The mock-path argument was: the session number is an independent judgment
rather than a summary, because the per-question inputs were frozen and the
output still moved 8 points. On the model users actually get, the output barely
moves, **and it already sits on the per-question mean without any instruction
telling it to.** We confirmed the instruction genuinely is not there: live
`response-analysis.json` v21 still asks `InterviewScorecard` for "overall
interview readiness" and `LiveRoundScore` for "readiness quality of this real
performance". Only `ConversationPracticeScore` says "the average of the
dimensions below", which predates all of this.

So the 13-point drop between the two runs is the model change from the routing
fix, not a prompt edit. What we were measuring on 2026-08-19 was sonnet-4-6.

**Your live-path finding is untouched by any of this.** You pinned the model to
the user lane when you measured, so your 8.4-point residual spread and the
rating label flipping 6 times in 10 are properties of the lane real users are
on. Ours were not.

Our read, and it is yours to accept or reject: ship the rollup on
`LiveRoundScore`, leave `InterviewScorecard` alone. Changing what the number
means on a mode where the number is already behaving costs comparability with
every stored score for no measured gain. If you would rather do both for
consistency, say so and we will take it, but we would want that stated as a
consistency call rather than a defect fix.

## Two things unchanged

1. **`scoring_version` (or any marker, any shape) on the response, bumped when
   the rollup lands.** Still absent; current top-level keys are `overall`,
   `headline`, `biggest_gap_title`, `biggest_gap_detail`, `per_question`. We
   still have not written the client half, deliberately, because writing
   against a guessed contract is what produced the `questions` vs
   `per_question` miscount. Name the key and we will persist it and refuse a
   delta across a version boundary, the same way we now refuse one inside the
   noise. Absent field reads as legacy.

2. **Verification needs a non-uniform session.** A flat set of per-question
   scores would show a stable overall even if the rollup were still ignored,
   and pass for the wrong reason. The fixture above (0.95 / 0.35 / 0.0) is a
   good case.

## On our side

`MockReport.scoreNoiseFloor` drops 10 to 4, one step above the measured 2, same
reasoning as before (2 is a lower bound: one input, eight runs, one call type).
At 10 we were suppressing movement that is really there.

No user ever saw the bad lane, so there is no user-facing discontinuity from
the routing fix. Only the harness was misrouted; every score on a device came
through the user lane all along.

## Still owed to you

The four `tr_brief_analysis` scenario-kind rows certified on 2026-08-19 are
harness-lane results and need re-running. Not done yet; this note was the
higher-value one to send first.
