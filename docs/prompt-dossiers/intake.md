---
call_type: tr_intake
config_slug: techrehearsal/intake
served_version: 7
model_dial: haiku-4-5 (all tiers, explicit)
recommended_model: claude-haiku-4-5-20251001
reconciled: 2026-07-24
---

# Intake (tr_intake)

## Intent

Warm, one-question-at-a-time conversational intake that gathers what the
prep needs: what's going on, with whom, what the user wants, worries,
history, good outcome. Returns `{next_question, done}` (plus `slots`
in entity mode). About 4 to 6 questions, explicitly a pace and not a
finish line. Personal scenarios keep a gentle register; negotiation and
pitch guidance is practical and numbers-forward.

## Shaping history

- 2026-06-18: discovered in prod logs as part of the personal-conversations
  surface; client-authored prompt at the time.
- 2026-07-08: brought under GP-owned managed prompts (migration close).
- 2026-07-23 #501: done-gate fix. Demo showed fragment utterances
  ("I need", "I") and the model declared "that gives me what I need"
  after 4 contentless turns. Root cause: "about 4 to 6 questions" read
  as a finish line. Now: count is a pace; done gates on the user's own
  answers covering what's going on, who with, and what they want;
  fragments count toward nothing; incomplete answer draws "I only
  caught part of that, say it again" (which also surfaces the client
  capture bug); closing line must reflect what was actually shared.
- 2026-07-23 #502: ENTITY MODE. When the client sends an ENTITIES TO
  COLLECT block (negotiations, pitch), the coach elicits entities
  conversationally, required first, extracts cumulatively into a
  `slots` field, and holds done until every required entity has a real
  value from the user's own words. New guidance for payNegotiation,
  purchaseNegotiation, negotiation, pitch.
- 2026-07-23 #504: no-dash rule extended to forbid spaced-hyphen
  imitation (models were emitting "word - word" to dodge the ban).

## Known failure modes and guardrails

- Finish-line counting: fixed by #501; watch for regressions where done
  arrives with near-zero cumulative user tokens.
- Upstream client truncation: TR client can send 1-2 token utterances
  (endpointing/send race, wire-proven 2026-07-23). The prompt now asks
  the user to repeat; the client bug itself is TR's to fix.

## Eval state

No dedicated grader fixtures (intake is not graded output). Quality is
judged by demo behavior and the wire signals the watchdog tracks
(turns per intake burst, user-token contribution per turn).

## Tuning rules

- Haiku is the deliberate model here (fast, cheap, adequate for warm
  single questions). If intake ever gains heavier reasoning (entity
  inference beyond stated words), re-evaluate before assuming Haiku
  still holds.
- The done gate must always be stated in terms of what the USER said,
  never in terms of turn count.
