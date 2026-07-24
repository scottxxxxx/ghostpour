---
call_type: tr_brief_analysis
config_slug: techrehearsal/brief-analysis
served_version: 5
model_dial: sonnet-4-6 (all tiers, explicit)
recommended_model: claude-sonnet-4-6
reconciled: 2026-07-24
---

# Brief Analysis (tr_brief_analysis)

## Intent

Turns the intake into the structured prep brief: counterpart,
your_goal, their_position, landmines, your_leverage, your_gaps,
prep_points. The brief is what the practice session runs on, so
whatever intake failed to collect surfaces here as thin fields.

## Shaping history

- 2026-06-18: discovered in prod (client-authored era), Haiku ~8s.
- 2026-07-08: GP-owned after the prompt migration; dialed Sonnet
  (inferential, structured output; same reasoning as tr_match_analysis).
- 2026-07-23/24 #498/#502/#504: second-person coaching voice; grounded
  practice openers ride the #502 work; spaced-hyphen ban.

## Known failure modes and guardrails

- Garbage-in amplification: with a contentless intake (the 2026-07-23
  fragment demo) the brief happily invents a plausible-sounding prep.
  The intake done-gate (#501) is the real fix; the brief should stay
  honest by leaving fields thin rather than fabricating specifics the
  user never said.

## Eval state

No dedicated fixtures. Cheap future check: feed a deliberately thin
intake and verify the brief marks gaps instead of inventing content.

## Tuning rules

- Sonnet stays while the output is inferential and structured; this is
  the same class of work as match-analysis.
- Fields must trace to what the user actually said in intake; a brief
  that reads impressive but ungrounded is the failure mode to test for.
