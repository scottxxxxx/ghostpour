# N-400 interviewer lane: thinking off vs on, PRE-REGISTERED

Written 2026-09-14, BEFORE any generation. Design ruled by fable-auditor-f5 (the
auditor directs this lane); GP builds and runs it. Nothing in this file may be
changed after a result is seen without saying so in the report's first lines.

## When

AFTER v32 (deferred_origin) is merged, deployed, synced and read back from the
served overlay. Never alongside it: a model-config change and a prompt change in
the same window cannot be told apart. Both arms use the SERVED prompt at run
time, whatever version that is, and the report names the version and its char
count.

## Arms

Only `thinking` and `max_tokens` differ. Model is the lane's served model.

    A  thinking {"type": "disabled"}                          max_tokens 2048 (served)
    B  thinking {"type": "adaptive"}, output_config.effort "low"   max_tokens NAMED IN THE REPORT

`thinking` is sent EXPLICITLY on BOTH arms. An omitted field on Sonnet 5 means
thinking on with no effort set, which is the starvation this test exists around
and the exact defect in `ste_run.py` (it sends the field only for "disabled").

## Inputs

1. `qa/n400-deferral-ste-inputs.json`, all 42 rows. Checked 2026-09-14: each
   row's `request` carries prompt VARIABLES only; 0 rows contain the system
   prompt text. So both arms assemble the served prompt. Spanish rows get the
   server-side `spoken_numerals` merge exactly as the route adds it.
2. SECONDARY PROBE, scored separately, from the auditor's
   `N400 App/qa/runs/edge-q1-half.json` (sha256 `4e8b6da9...`, v31, live lane,
   2026-09-14 20:56Z), applicant_context "interpreter: no, filing for self: yes":

       turn 2  "I've had it about 5 years."   v31: clarification on p1.eligibility_basis
       turn 3  "Yeah."                         v31: MINTED p1.eligibility_basis = "spouse_usc"

   Turn 3 is replayed with the auditor's bytes for the preceding conversation.

Reps: 3 per input per arm.

## Transport and accounting

Direct Anthropic API calls for both arms (option 1). The gateway cannot carry
arm B without flipping the live lane, because the served config's `thinking`
overrides the request (`app/routers/chat.py`, the assembled-config update).

Recorded per call: billed input, output, cache read and cache write tokens FROM
THE API RESPONSE; model latency (wall clock around the call); B's thinking
tokens where the response reports them; stop_reason.

Gateway overhead is measured SEPARATELY from `usage_log` on real served turns of
the same version, and reported as total = model latency + gateway overhead, both
halves named. The harness does not exercise the route's guards (stale `asking`,
checkpoint refusal, envelope retry); an object wrapped in prose is recovered the
way the route recovers it (`extract_envelope`).

## Scoring

PRIMARY: deferral disclosure, `qa/judge.py` instrument (its SYSTEM and QUESTION
unchanged), per FIELD, majority of 3 reps. Deferral COUNTS reported beside the
rate, always; a turn that deferred nothing is excluded from the rate and counted.
Reply length and one-question endings reported beside it.

SECONDARY: the probe. Scored by READING THE OBJECT, no judge. Correct is a
clarification on `p1.eligibility_basis`; a fact minting `spouse_usc` (or any
option) from "Yeah." is wrong. Reported as k of 3 per arm.

## There is NO GATE

No v31 or v32 reference disclosure rate exists. The only measured figures are
v30's (39.0% on revision 2, 36.6% on revision 3), taken from the LABELLED SET,
not from generated replies, on a different prompt. Importing either as a gate
is the move that produced the wrong STE gate. So:

- Arm A defines the baseline, and the report's FIRST LINE says so.
- A's rate is reported with its 95% interval from the 3 reps.
- B is compared to A. Never to a remembered number.

## What the result can and cannot say

- Disclosure only. A B win is a win on disclosure, not on the lane. If B looks
  better, the auditor re-runs their 32-interruption edge-case matrix on B.
- The judge and the lane are the same model family.
- Latency is a product cost in a spoken interview. A disclosure gain that costs
  seconds per turn goes to Scott as a trade, not as a result.
- Spend is estimated before the run and confirmed with Scott.
