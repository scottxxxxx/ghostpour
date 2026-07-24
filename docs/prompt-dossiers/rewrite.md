---
call_type: tr_rewrite
config_slug: techrehearsal/rewrite
served_version: 6
model_dial: haiku-4-5 (all tiers, explicit)
recommended_model: claude-haiku-4-5-20251001
reconciled: 2026-07-24
---

# Rewrite / Say It Better (tr_rewrite)

## Intent

Takes a line the user wants to say and rewrites it to land better while
keeping their voice, intent, and length. Returns `{rewritten, why}`.

## Shaping history

- 2026-06-19: first seen in prod (client-authored era).
- 2026-07-16 #458: mandate strengthened after Scott's live finding
  (rewrite ~90% identical to a WEAK-rated line). Was wording-polish
  only; now "fix the APPROACH, a tidied weak approach is a failed
  rewrite", and it consumes an optional HOW IT WAS ASSESSED payload
  section so the named weaknesses from the analysis verdict get
  cleared. Client side composes that payload (TR owes the wiring;
  also owed: the scenario_kind bug where the Say-it-better sheet sent
  jobInterview during a hardConversation session).
- 2026-07-23/24 #504: spaced-hyphen ban.

## Known failure modes and guardrails

- Cosmetic rewrites of weak approaches: the #458 mandate. Product bar:
  if the analysis rated a line WEAK, the rewrite must be materially
  better or the labels are lying.
- Wrong-scenario coaching (STAR voice on a personal conversation) when
  the client sends a stale scenario_kind: client bug, but the prompt
  should stay robust to kind mismatch by leaning on the line itself.

## Eval state

No dedicated fixtures. Candidate future eval: pairs of (weak line,
analysis verdict) with a judge asking whether the rewrite clears the
named weaknesses. Seed from the #458 incident shape.

## Tuning rules

- Haiku is the explicit dial and has been adequate since the mandate
  carries the heavy lifting. If rewrites start ignoring the HOW IT WAS
  ASSESSED section, test Sonnet before rewriting the mandate again.
- Keep the user's voice: a rewrite that sounds like a coach instead of
  the user is a failure even when strategically correct.
