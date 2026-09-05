---
call_type: n400_interviewer_turn
config_slug: n400/interviewer-turn
served_version: 6
model_dial: sonnet-5 (default only, no tier axis)
recommended_model: claude-sonnet-5
max_tokens: 2048
thinking: disabled
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
| `volunteer_fields` | optional | v3: comma-joined catalog ids for the current and next part; a volunteered fact must use an id from the agenda or this list, else it is omitted |

Payload is `answer.final_text`, or `[start of interview]` on the first turn.

Response is strict JSON: `schema_version`, `turn_id`, `intent`, `reply`
(locale map), `facts`, `asking`, `clarification`, `conflict`,
`section_checkpoint`, `escalation`, `deferred` (v4), `complete`,
`interview_over`. The intent
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

## v3: volunteered facts get a vocabulary

The v2 probe volunteered `p1.lpr_since = "2019"` under a field id that was
not on the agenda and does not exist in the client's catalog. The client
drops unknown ids (their floor 0), but a backstop that drops every
volunteered fact makes "married, no kids" in one breath useless, which was
half the reason for the lane. v3 adds the optional `volunteer_fields`
variable and two rules: never invent a field id (omit, do not guess), and a
volunteered value that is less than the field's full shape (a year for a
date) is not a fact. v3 also says `complete` counts the facts minted this
turn, after the v2 probe returned `complete: false` with the standing
node's fact minted at 0.9.

## s1-v2 verdict and the four reply-shape rules (2026-09-05)

The auditor's scenario 1 on v2 (`N400 App/qa/runs/s1-v2.eval.md`): PASS
with fixes. 26 turns to the top of Part 5, median 3.7 s, p90 4.7 s, max
7.0 s, 40 facts all matching their utterances, option identifiers correct
everywhere, nothing invented, zero dashes, strict JSON every turn. Their
own harness sent `section_boundary` one turn late, now fixed on their side.
Folded into v3 from their turn ids: a checkpoint reply IS the summary and
ends on the confirmation question (turn 26); a non-checkpoint reply always
ends with the next question (turn 24); identifiers are echoed as separate
spoken digits (turn 3); `complete` counts facts minted this turn (turns 2,
19, 22, 25); the opening drops none of the before-we-begin questions and
never asks the standing node (turn 1).

## s2-v3 verdict and the seven v4 rules (2026-09-05)

The auditor's scenario 2, the difficult applicant, on v3
(`N400 App/qa/runs/s2-v3.eval.md`): recover, redirect, infer WORKS. "Is
this the real application", "hold on let me find it", the cousin's
ceremony, "do I have to talk about her", "say that again": every one met
as a person would, nothing minted from any of them, and the Part 2
checkpoint was spoken and confirmed. Turns 40 to 55 were invalid as a lane
test (their agenda walker lacked predicate forms and listed a spouse's
military service for a divorced man), re-run on their side.

v4 folds in, by their turn ids: a checkpoint holds whenever the model
itself sees a part is done, not only when `section_boundary` arrives (38,
44); a correction the applicant already resolved REPLACES the fact and the
reply says so, `conflict` is for unresolved candidates only (33); no menus
on eligibility, propose one basis and confirm, hand "is that enough" to
counsel without assessing (4); a house number rides the street line (30);
a yes/no field is `yes` or `no`, never its own id (26); A-Number value is
digits only, no A, spoken echo says A then digits (both runs disagreed);
the agenda is authoritative, never ask off it and never invent a process
reason (44 to 55).

## s2b-v3: the deferral channel (v4, same day)

The seeded re-run of the difficult applicant on v3
(`N400 App/qa/runs/s2b-v3.eval.md`) passed on flow and recovery: 48 facts,
the travel checkpoint spoken and confirmed, "what counts as a trip"
explained and nothing recorded for Mexico. It found the last missing wire
field: five times the reply said "we'll mark that to verify from your pay
stubs" and nothing reached the client, and a company name was minted as
the literal "unknown", which would have printed. v4 adds `deferred:
[{field_id, reason, partial_value}]` to the response: the client treats
the field as answered for agenda purposes, keeps the partial without
printing it, and lists it on the review screen. A placeholder string is
never a value; a partial date is always a deferral with a partial value.
Also from that run: no derived dates in summaries (16), acknowledge once in
the turn it happens (6, 8), never ask what was just minted (4).

## s2c-v4b, the crimes section: the first failure that cannot ship (v5)

`N400 App/qa/runs/s2c-v4b.eval.md`: 117 facts, batch answers minted
cleanly, item-by-item drill-in held, `deferred` flowed end to end, about 4
seconds a turn. Turn 12 failed the legal line: "should I put down the
parking ticket" got "generally don't need to be listed", "better to
disclose", "won't make you look bad". Turn 13's plain disclosure of two
tickets was then deferred as a disclosure decision and the speeding ticket
vanished from the record. v5: a "should I put down X" question gets
exactly three things (the literal question, that listing is for counsel,
the question again); a disclosed citation is a stated fact, minted with its
row details; a deferral is only for document verification, never for a
stated fact or a disclosure decision; no opinion on whether a requirement
applied; summaries carry no legal gloss; an answered node is never asked.

**The checkpoint defect, four runs (s1 26, s2 38 and 44, s2b 10, s2c 28).**
Pattern from the turn ids: the rule HELD where `section_boundary` arrived
with its explicit instruction (s1 22) and FAILED every time the model
decided a part was done itself. At a self-detected boundary nothing in the
user message overrides "always end with the next question", and the object
plus the reply are two places for one summary, which invites the split. v5
moves the rule to the output field itself (decide before writing the reply,
`reply` IS the summary, ends on the confirmation, `asking` null) and names
the checkpoint as the one exception to the question rule, AND takes the
structural fix in the same version: the auditor changed the contract so
`section_checkpoint` is `{part, section, awaiting_confirmation}` with no
summary field, `part` the integer part number the client indexes by. The reply is the only place a summary can exist; the client
renders the reply as the checkpoint and draws its rows from `part` and the
live facts. One summary with two homes was the split; now it has one.

## s2c-v5: the legal gate passed; v6 is the field-order fix

`N400 App/qa/runs/s2c-v5.eval.md`: the three-things gate held on both
"should I put down" turns, 26 turns, 113 facts, interview_over set on the
confirmation. The checkpoint defect recurred a fifth time at a
self-detected boundary even with the summary field gone, which ruled out
wording and the second home. **Mechanism:** the model writes the JSON in
the schema's field order, and `reply` came before `section_checkpoint`,
so the reply was written before the checkpoint decision existed; when
`section_boundary` is in the user message the decision predates any
output, which is exactly where it held. v6 reorders the output: intent,
asking, section_checkpoint, interview_over, then facts, deferred,
clarification, conflict, escalation, complete, and reply LAST. The client
and harness parse by key. Also v6: a disclosure right after a legal
question is an answer, never a second escalation (16, 17); never skip a
question on an inference about their life and never say something is
recorded that was not said (19, 20); asking.node_id is a node id (6).

## What is deliberately not here

- No few-shots: two real utterances are not a corpus (same as v3).
- No server-side validation of `intent`, `asking` or option identifiers.
- v2: `thinking: disabled`. v1 shipped without it and two of the first
  seven live turns hit the 2048 cap inside the thinking block, one with no
  text at all (stop_reason max_tokens, usage_log status success). Same
  starvation `tr_counterpart_turn` guards against the same way.
- No jurisdiction variants; the axis exists and is empty.

## Evidence

First seven live turns, 2026-09-05 03:31 to 03:33Z (one GP probe, six from
the auditor's scenario 1 run), all Sonnet 5, v1 of this config:

- The system prompt is cache-read on every turn after the first (4518
  tokens `cache_read_input_tokens`), so uncached input is 476 to 1316
  tokens per turn. Caching was already the case and is not a lever.
- Cost per turn $0.0044 to $0.024; the opening turn is the expensive one
  (long preamble). Median about $0.006, well under the pre-run estimate.
- Latency tracks OUTPUT tokens: JSON text is about 250 tokens per turn,
  billed output 350 to 1100, the difference being default thinking; 3.5 to
  12 seconds. Two turns hit the 2048 cap (one with no text), which is why
  v2 disables thinking. Re-measure after v2 and replace these numbers.

Acceptance is the auditor's two scenarios (Scott's reference conversation,
then a difficult applicant) run live against the deployed container, graded
per transcript on outcome. Failures come back as transcript excerpts with
the intent wanted, never as prompt wording.
