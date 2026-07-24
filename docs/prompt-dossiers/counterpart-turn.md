---
call_type: tr_counterpart_turn
config_slug: techrehearsal/counterpart-turn
served_version: 6
model_dial: sonnet-4-6 (all tiers, explicit, #505)
recommended_model: claude-sonnet-4-6
temperature: 0.8
max_tokens: 300
reconciled: 2026-07-24
---

# Counterpart Turn (tr_counterpart_turn)

## Intent

The live in-character counterpart: per-turn spoken replies in the
persona from the brief, reacting to what the user actually said,
emotionally realistic, one to three sentences, never coaching, never
breaking character. Returns `{line, inner_state, conversation_over}`.
Client owns the session loop (one call per user turn).

## Shaping history

- 2026-07-16 #459: lane created (config + call type) with continuity
  mandate (never re-ask what was answered), per-kind realism, temp 0.8.
- 2026-07-20 #464: persona slot rendered empty on some scenarios; falls
  back to scenarioDefaults.
- 2026-07-23 #503: OFF-STAGE MOMENTS mandate. Truck-purchase demo: the
  salesperson exited to "talk to my manager", user said OK, scene
  parked on filler lines. Now the model owns every character except the
  user, including people not in the room; by its next line the
  off-stage beat has already happened and it returns carrying a
  realistic result; a short user acknowledgment (OK, go ahead) is the
  cue to skip the wait; dead-time beats (numbers, paperwork, calls)
  resolve between lines. Wire shape unchanged, zero client work.
- 2026-07-23 #502 (TR-side session): OPENING THE SCENE. When the
  conversation contains only a start-of-conversation marker, the
  counterpart speaks first, grounded in the brief's specifics (a
  salesperson greets the customer about the exact item they came for);
  generic "what's on your mind" openers banned when the brief says why
  both parties are here. The TR client generates this opener in the
  background during setup, with hardcoded canned lines as fallback
  (fallback templates moving to served config, TR work in flight
  2026-07-24).
- 2026-07-23 #504: spaced-hyphen imitation banned alongside dashes
  (models were faking dashes with "word - word", caught live in a
  generated opener).
- 2026-07-23 #505: model dial added. The lane had NO routing row and
  fell to the tier default (Haiku) whenever it ran, despite
  recommendedModel Sonnet. Now dialed Sonnet 4.6 at every tier and
  live-proven.

## Known failure modes and guardrails

- Parked scenes: any beat whose advancement depends on a non-user
  character must resolve off-stage (#503). Watchdog heuristic: bursts
  of counterpart turns with tiny input deltas and no progression.
- Silent model downgrade: no dial means tier-default Haiku, which
  noticeably flattens improv (observed 2026-07-23). The dial exists
  now; the watchdog cross-checks dial vs recommendedModel vs the
  models actually recorded in usage_log.

## Eval state

No grader fixtures (it is the roleplay, not the grading). Judged by
demo realism and wire signals. If a fixture set is ever built, seed it
from demo transcripts of stuck or flat scenes.

## Tuning rules

- Sonnet at temp 0.8 is the intended combination (#459, reaffirmed
  #505). Off-stage improvisation is exactly the work a smaller model
  does worst; do not downgrade this lane on cost without a
  side-by-side.
- Keep replies short (max_tokens 300 is a feature): the counterpart
  talks like a person, not a narrator.
- Never add stage directions; off-stage beats resolve in dialogue.
