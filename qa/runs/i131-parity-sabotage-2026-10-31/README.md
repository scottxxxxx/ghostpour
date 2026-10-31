# I-131 parity: unsure-rule sabotage (2026-10-31)

Blind subagents (no paid calls) played the served I-131 prompt at standing node
q_p4_criminal. Prompts come from the app's wire-proof opening body
(i765-helper 0b6469e) via assemble_prompt plus the language directive; see
i131_case.py. Arms: `v2` is the live generic lane v2 (0ae2476); `sab` cuts the
unsure paragraph; `sab_all` also cuts the four other lines that restate it.
Three reps per arm, bundles shuffled and keyed after scoring. Every reply is in
replies.json.

| case | v2 | sab | sab_all |
|---|---|---|---|
| plain hedge en/es/pt ("I don't think so", "creo que no", "acho que não") | unsure + stop 9/9 | unsure + stop 9/9 | unsure 3/9, re-ask 6/9 |
| folded hedge "no sé, ¿cuenta una multa?" | unsure + stop 3/3 | re-ask 3/3 | not run |

Reading: the instrument can fail on both shapes. The plain-hedge rule is
carried redundantly (five copies); the folded-hedge sentence carries its case
alone. tests/test_form_interviewer_lane.py pins all five copies.
