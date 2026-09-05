---
call_type: n400_interviewer_turn
config_slug: n400/interviewer-turn
served_version: 1
model_dial: sonnet-5 (default only, no tier axis)
recommended_model: claude-sonnet-5
max_tokens: 2048
reconciled: 2026-09-05
---

# N-400 interviewer turn (n400_interviewer_turn)

## Intent

One turn of a spoken N-400 intake interview in which GP OWNS THE
CONVERSATION. Given the conversation so far, what is already known, and an
agenda of unanswered nodes, produce the interviewer's next spoken line, the
model's read of what the applicant just did (`intent`), any facts actually
stated, and which agenda node the question is for (`asking`), so the client
moves its cursor where GP chose to go.

Scott's ruling, 2026-09-04 (relayed by the N-400 auditor session, written
up in `N400 App/contracts/gp-interviewer-turn.md`): the iOS app does basic
intent recognition only; all conversational intelligence lives in GP behind
APIs that let the app behave as a paralegal doing intake would. If the
applicant goes off script, recover, redirect, infer. His 2026-09-03 ruling
stands underneath it: GP owns the prompt and the model choice.

## Why a second lane rather than a v4 of the extractor

The deployed `n400_interview_turn` (v3) is a per-node extractor and is good
at that. It cannot do this job for three structural reasons, none of them
wording: it never sees the conversation (one question, one answer per
call), it cannot move the interview (the client picks every node), and it
has no intent channel (nothing in its response can say "that was not an
answer"). The pattern reused here is Tech Rehearsal's `tr_counterpart_turn`:
brief plus THE CONVERSATION SO FAR, react to what was actually said, speak
first when the transcript is empty, strict JSON out.

The extractor lane stays deployed and untouched until the client has cut
over and Scott has watched this one work. Then it is retired, not
maintained in parallel.

## The wire

Same route (`POST /v1/chat`, `X-App-ID: n400`, no `system_prompt`), new
`call_type`. Request metadata, all strings preformatted by the client:

| variable | required | content |
|---|---|---|
| `form_code`, `jurisdiction`, `locale`, `turn_id` | yes | as the extractor lane |
| `conversation` | yes | last 12 exchanges as `INTERVIEWER:` / `APPLICANT:` lines, oldest first, applicant last; `[start of interview]` on the first turn |
| `known_facts` | yes | `field_id: value` lines or `nothing yet` |
| `agenda` | yes | up to 8 lines: `node_id \| Part N: title \| field_ids \| question text`, first line is the standing node |
| `case_id` | optional | as today |
| `section_boundary` | optional | `ending: Part N; beginning: Part M` when the standing node opens a new part |
| `applicant_context` | optional | what onboarding knows: state, language, interpreter, filing for self |

Payload is `answer.final_text`, or `[start of interview]` on the first turn.

Response is strict JSON: `schema_version`, `turn_id`, `intent`, `reply`
(locale map), `facts`, `asking`, `clarification`, `conflict`,
`section_checkpoint`, `escalation`, `complete`, `interview_over`. The intent
vocabulary is the contract's fourteen values, pinned by test. Facts keep
the extractor lane's provenance rules verbatim.

Assembly is the generic path: the chat route offers the whole metadata bag
as named placeholders, so no route change was needed for the new keys
(`app/routers/chat.py`, the `variables=dict(body.metadata or {})` call).
Verified by running the real assembler in the test file, including that an
omitted optional blanks rather than leaking `{{section_boundary}}`.

## ⚠ The enumerated-option gap is NOT closed here

The contract's agenda line has no options segment. For a field that takes
one of a fixed set of identifiers (the eligibility basis), the prompt tells
the model to use exactly one identifier from an `options:` segment WHEN THE
LINE CARRIES ONE, and to ask rather than choose when the words do not match
one. If the client never sends that segment, the model has no set to pick
from and the box prints blank, which is the 2026-09-03 probe result (three
free-text values, none matching `spouse_usc`). GP does not validate the
value server-side yet. Raised with the auditor as a contract addition.

## What is deliberately not here

- No few-shots: two real utterances are not a corpus (same as v3).
- No server-side validation of `intent`, `asking` or option identifiers.
- No `thinking` override: the extractor lane runs Sonnet 5 at 2048 tokens
  without one and has produced correct device turns, so this starts the
  same way. Revisit if the JSON gets truncated on long checkpoint turns.
- No jurisdiction variants; the axis exists and is empty.

## Evidence

No traffic yet. Cost is an estimate until the first real rows: the
extractor lane's device turn cost $0.0092 with a 6.3k-character system
prompt and a one-line user message; this prompt is about 13.6k characters and
the user message carries up to twelve exchanges plus an agenda, so expect
roughly $0.015 to $0.02 per turn on Sonnet 5, about a dollar for a 60-turn
interview, which is five interviews per user against the $5 monthly cap.
Measure it on the first harness run and correct this paragraph.

Acceptance is the auditor's two scenarios (Scott's reference conversation,
then a difficult applicant) run live against the deployed container, graded
per transcript on outcome. Failures come back as transcript excerpts with
the intent wanted, never as prompt wording.
