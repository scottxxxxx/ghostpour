---
call_type: tr_mock_interview
config_slug: techrehearsal/mock-interview
served_version: 4
model_dial: sonnet-4-6 (all tiers, explicit)
recommended_model: claude-sonnet-4-6
reconciled: 2026-07-24
---

# Mock Interview (tr_mock_interview)

## Intent

The interview-scenario practice generator: questions grounded in the
parsed JD and resume, interviewer persona where research provides one.
Predates the live counterpart lane and remains the interview path.

## Shaping history

- 2026-07-05: entitlement cluster flip (B2 era).
- 2026-07-08: GP-owned after the prompt migration; Sonnet dial.
- 2026-07-23/24 #504: spaced-hyphen ban.

## Known failure modes and guardrails

- No recorded incidents. The structural risk is divergence from the
  counterpart lane: as tr_counterpart_turn gains realism rules
  (continuity, off-stage beats), interviews run through this older
  scripted path and can start feeling stiffer by comparison.

## Eval state

Interview beat exists in ~/tr_eval fixtures (grader side). Kimi
baseline beat Sonnet on that single beat 2026-07-17 (authorship-bias
caveat, single beat, not actioned).

## Tuning rules

- If interviews ever move onto the live counterpart lane, port the
  continuity and off-stage mandates rather than re-deriving them, and
  eval the interviewer persona against research-interviewer output
  first.
