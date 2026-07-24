---
call_type: tr_compare_reality
config_slug: techrehearsal/compare-reality
served_version: 6
model_dial: sonnet-4-6 (all tiers, explicit)
recommended_model: claude-sonnet-4-6
temperature: 0.2
reconciled: 2026-07-24
---

# Compare Reality (tr_compare_reality)

## Intent

Plan-anchored comparison of how the real conversation went versus the
rehearsal: what matched the prep, what surprised, what to adjust next
time (#374 origin).

## Shaping history

- 2026-07-02 #374: config created (plan-anchored real-vs-rehearsal
  comparison).
- 2026-07-08: GP-owned after the prompt migration; Sonnet dial.
- 2026-07-23/24 #504: spaced-hyphen ban.

## Known failure modes and guardrails

- No recorded incidents. The risk profile is hindsight distortion:
  grading the plan by the outcome ("it worked so the prep was right")
  instead of by what the plan could have known.

## Eval state

No dedicated fixtures.

## Tuning rules

- Low temperature (0.2): comparisons should be reproducible.
- Anchor every claim to either the brief or the real-conversation
  account; unanchored coaching here reads as generic advice and
  undercuts the feature's premise.
