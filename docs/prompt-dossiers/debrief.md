---
call_type: tr_debrief
config_slug: techrehearsal/debrief
served_version: 6
model_dial: sonnet-4-6 (all tiers, explicit)
recommended_model: claude-sonnet-4-6
temperature: 0.2
reconciled: 2026-07-24
---

# Debrief (tr_debrief)

## Intent

End-of-session debrief across the whole practice conversation: what
went well, what to work on, concrete next steps. Tags whatever scenario
the user was in (shared-flow call).

## Shaping history

- 2026-06-19: call type first confirmed in the TR scenario contract.
- 2026-07-08: GP-owned after the prompt migration; Sonnet dial.
- 2026-07-23/24 #498/#504: second-person coaching voice ("you did"
  instead of "the user did"); spaced-hyphen ban.

## Known failure modes and guardrails

- No recorded incidents yet. Watch the same axis as response-analysis:
  scenario-appropriate framing (a negotiation debrief praising empathy
  over outcome, or a personal-conversation debrief scored like an
  interview, would be the bleed to catch).

## Eval state

No dedicated fixtures. If graded-debrief quality becomes a question,
extend the ~/tr_eval fixture format with full-session transcripts.

## Tuning rules

- Low temperature (0.2) keeps the debrief consistent run to run.
- Debrief consumes the same session the counterpart lane produced;
  when counterpart realism changes (#503 off-stage beats), spot-check
  a debrief to make sure it treats off-stage results as real events.
