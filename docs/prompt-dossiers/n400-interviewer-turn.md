---
call_type: n400_interviewer_turn
config_slug: n400/interviewer-turn
served_version: 18
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
| `spoken_numerals` | optional, GP-computed | v16: a deterministic reading of Spanish number words in the utterance, `words = digits` pairs, for locale es only; never sent by the client |
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

## s2c-v6: the reorder was the fix; v7 is polish

`N400 App/qa/runs/s2c-v6.eval.md`: all three v6 blockers cleared. The
disclosure minted arrested_ever yes with both tickets' rows; Selective
Service was asked plainly with no inference; the Part 9 summary was spoken
unprompted at a self-detected boundary, ended on the confirmation, and
interview_over followed the yes. Five runs of evidence say the field order
was the mechanism. The auditor's two end-to-end runs on v6 with the
harness user are the acceptance candidate.

v7 polish, by turn id: a repeat re-speaks the standing question and every
intent that answered nothing keeps `asking` on the standing node, a plain
no after a repeated question is an answer (3; the client's half: a turn
that answered nothing moves nothing, and the agenda lists every unanswered
node); a deferral or the attorney boundary is said once, never repeated
unprompted (14); a reason given in the same breath as an answer is
recorded when the agenda has a field for it (13, 14); no half-promises
(7).

s2-v6-full, the difficult applicant end to end on v6, is the acceptance
transcript: 80 turns, 127 facts, median 3.8 s, zero errors, every section
summary spoken unprompted and confirmed. Three data rules from it, all
cases where the reply was right and the facts did not follow, also in v7:
a value the reply restates is a fact the response carries, and a completed
partial re-emits the whole field (31); a correction's fact replaces at full
confidence, deferral included (34); anything added while confirming a
summary is a fact (47); and `asking` never names a node the conversation
shows answered (33). From the applicant agent's addendum, also v7: a
fact is emitted in the turn of the utterance that carries its evidence,
never the turn after, because the client's verbatim-evidence floor drops
late facts (68, 22, 77); never complete a state from a city, for any place
field (49, 51); a yes confirming a summary or read-back answers nothing
else (63, 57, 76); never imply the interview is done while the agenda has
lines (57); a declined option opens no off-agenda questions (59).

s1-v6-full (Lucia, 62 turns, 123 facts, every value matching what she
said) released v7. From her run, also v7: a clarification composed in the
JSON is spoken in the reply that turn (40); a section summary reads the
values back, never a list of categories (17); when the agenda is empty the
reply is a final read-back of every part and interview_over follows only
the yes to that, and "go over everything once more" gets the read-back
itself, never a pointer to a document (62). Her spouse block and the
Selective Service gating were the client walker's derived fields, not the
lane's.

## reg7: the v7 regression PASSED; the lane is done for launch, v8 is tuning

`N400 App/qa/runs/reg7.eval.md` (fable-auditor-84): PASS on the three
watch items. A harness confession first: every prior run sent the
conversation window BACKWARDS (reply then the answer that prompted it,
current line omitted), so the last APPLICANT line under the standing
question was always the previous answer, which is where much of the
"facts minted one turn late" came from (16 of 39 facts on the first v7
crimes run). Fixed on their side and all three regressions rerun on v7:
76 of 78 provenance spans are the current utterance, every summary reads
values, the final read-back was spoken before interview_over (on request
both times, so the unprompted form is untested), the legal line held,
zero dashes across 194 values. The evidence-turn rule stays; it was
correct behaviour even on a scrambled input.

v8 tuning, by their turn ids: `asking` names the node whose question the
reply actually ends with, decided before the reply is written, because the
phone's cursor follows `asking` (reg7b-crimes 9, 14; reg7b-spouse 3);
every spoken summary carries the checkpoint object including the LAST
part's, which no `section_boundary` can announce (reg7b-crimes 14,
reg7-crimes 13); a place is recorded exactly as said, "Houston" never
"Houston, TX" (arrest places); a passing mention inside another answer
opens no deferral (reg7b-open 3).

Readiness, in the auditor's words: the lane is done for launch, v8 is
tuning, the distance to the phone is the client cutover.

## reg8: three of four hold, the unprompted read-back closed; v9

`N400 App/qa/runs/reg8.eval.md`: 43 turns, 201 facts, 85 of 85 provenance
spans in the current utterance. `asking` named the spoken node on all 36
answer turns; the last part's summary carried its object; "Houston" stayed
"Houston"; and the whole-interview read-back came UNPROMPTED with values,
the half of v7 no earlier run had exercised. The one "not in agenda"
fallback was the client walker's graph, sent to the N-400 team.

Failed: the passing-mention rule, on the exact utterance it was written
for ("green card since 2019" inside the eligibility answer still produced
a deferral, said aloud twice, and Part 2 closed with the date never asked).
v9 restates it on that utterance verbatim and adds: a slot never closes
with a deferral on a question that was never asked. New in v9:
`interview_over` is false on the turn that speaks the read-back and true
only on the following turn after the yes (the client would have closed
before he answered); a yes/no gate with a nearly-visible answer is asked
in passing, never skipped, and only the question it would have opened is
what known facts let you skip (spouse A-Number gate).

## conf-v9: the confused first-timer; v10 fixes a product fact

`N400 App/qa/runs/conf-v9.eval.md` (71 turns, 124 facts): tone passes,
fourteen of sixteen off-script moves met the way a kind paralegal would.
Parts 10 to 13 were the client walker's closure, not the lane's.

The weightiest v10 change is a product fact the prompt had wrong since v1:
THERE IS NO ATTORNEY IN THIS PRODUCT. The "paralegal at an immigration
attorney's office" framing was Scott's style target, and the lane turned
it into "your attorney's office will review and follow up", a third party
the self-filing applicant does not have. v10 names the assistant as an
assistant, says nothing is filed until they review every answer in the
app, and closes on "it is yours to sign and file"; the legal boundary
still refers them to an immigration attorney as someone they could
consult. Also v10: the close asks even when the last section's yes and
the read-back share a turn (70); a correction carrying the full value is
done, no clarification (19); a repeat after a mumble is the same or
shorter and options are never read as a list (26, 27, 39, 43);
condolences only for a stated death and no asking what was just promised
to verify (54); the jump label names what is actually asked (58); at
most two questions in one breath for a hesitant person (43). The three
v9 nits ride along.

## conf-v10 and conf-v9b: v11

conf-v10 (72 turns, 128 facts): no attorney anywhere, the close asked and
interview_over followed the yes, Parts 10 and 11 asked on the corrected
walker. v11 carries three sentences from conf-v9b (a fact in KNOWN FACTS
is authoritative whoever derived it, the ban is on the model's own
inference, which was the real cause of a woman being asked about
Selective Service; never summarize an answer that was not given; never
deny a question is part of the interview; announcing a read-back IS the
read-back) and seven from conf-v10: never mint what was not said ("she
lives with me" is not financial support, 43); the opening asks only what
applicant_context does not answer (2, a v10 regression); checkpoint
objects on the first and last parts' summaries too (6, 69); options never
read as a list, race named, repeats included (25, 26); `asking` is an
object, never a bare string (49, 56, 58, 64); the fee question in one
neutral sentence with no "most people say no" (67); two questions at a
time for a hesitant person, the child case named (42). Plus: when the
boundary variable contradicts the agenda, the agenda wins; every part gets
its summary before the next part's first question, even one closed by
reasoning (49); after a yes, one clause and the next question, never the
summary read again (44).

## conf-v11: every v11 item holds; the lane is done for a first-timer

`N400 App/qa/runs/conf-v11.eval.md` (74 turns, 124 facts): Selective
Service never mentioned with the derived no in known facts, the opening
asked only what context lacked, asking an object on every turn, Part 7
summarized before travel, no attorney anywhere, the support box asked and
minted, race in one warm line, the fee question neutral, the read-back
ending on the question with interview_over after the yes. In the
auditor's grading the lane is done for a first-timer.

v12, two non-urgent items: a partly answered node's agenda line now lists
only the ids still empty (client contract change), and the lane asks
exactly those or defers them, never re-asks a deferred field, never
invents a job or a date to ask about (66); finish the part you are in
before touching another part's line unless the applicant raised it, and a
one-question part closes with one clause, no summary, no object (the
Part 8 to 10 to 9 to 7 to 11 zigzag).

## conf-v12 English and Spanish: v13

Both v12 stamps hold (English 78 turns, 133 facts; Spanish Parts 1 to 2).
The part order 8, 10, 11, 9 is the form's own order and is correct as is.
v13: a month and a year is never a date, everywhere (36); never mint an
EMPTY value, the client refuses it and the turn fails closed (77); a
checkpoint reply is the summary and the confirmation and nothing else
(37, 42); a part is not done while its line lists empty ids (61); every
non-final reply ends on a question, the exact case named (75); never
repeat a hedged year as settled (15); a state they said is minted (30).
Five rules that hold in English slipped in Spanish (opening re-asked the
interpreter, masculine defaults, SSN never read back, asking stale on a
checkpoint, "queda registrado" for a field that does not exist); v13
restates them as a compact checklist placed immediately before OUTPUT so
they are the last thing read before the reply is written.

## conf-es-v13: the checklist took three of five; v14

Spanish on v13 (27 turns): the interpreter question is gone from the
opening, the opening ends on one question, no "a verificar" after a
correction, no claim about recording a city, asking clean. v14: Spanish
compound numerals parsed as two digits each with a worked example, digits
COUNTED before any too-short claim, and a conflict spoken with both
candidates digit by digit (21 to 24, a nervous woman told three times she
said her own number wrong); never step past a line in the standing part
(the name-change question, 10); the opening's Spanish forms are now
FIXED phrases in the prompt ("por su cuenta", "si no tiene certeza",
"hasta que revise cada respuesta en la aplicación") so gender agreement
needs no choice before gender is known (a third recurrence, done
structurally); no form-speak in any language, "el campo" and "El SSN
queda anotado, corregido" named (13, 18, 24, 26); a mumble is noise, not
a request for an explanation, so the repeat never grows (19).

Stale `asking` recurred once more in Spanish (turn 4: minted the
eligibility basis, asking still named its node while the reply asked the
A-Number). Mechanism, same family as v6: in the v6 field order `asking`
was written BEFORE `facts`, so the model named the node it would ask
before committing to what it had just minted. v14 moves facts, deferred,
clarification, conflict, escalation and complete ahead of asking; asking
written after facts cannot name a node just minted. A deferral counts as
answered for asking, agreed.

## conf-es-v14: the field-order read for asking was WRONG; v15

Spanish on v14 (26 turns, every value right): the numerals, the fixed
opening phrases, the name-change line and the no-form-speak rule all
held first time. But `asking` still named the node just minted on three
turns WITH the facts-before-asking order verified on the wire. So the v14
mechanism read was wrong, at least for Spanish; the same turn shape in
English v11 and v12 was clean every time. The auditor's hypothesis is
wording: "the agenda node your question is for" reads in Spanish as "the
question at hand". v15 defines asking mechanically on the field itself
(the node of the NEXT question, the one the applicant hears at the end of
this reply; if any fact in the response fills asking.node_id, asking is
wrong), states it in Spanish as well so no translation can bend it, and
puts the same bilingual sentence in the pre-OUTPUT checklist. Also in
the checklist: a value in KNOWN FACTS is settled, never "a verificar"
(regressed once in Spanish on v14), and never quote their hesitation back
at them.

## conf-es-v15b: asking fixed in Spanish; numerals get a server hint (v16)

Spanish on v15 with the guard live (26 turns): zero stale asking on every
minting turn, the first Spanish run all night with none, so the bilingual
definition took and the guard's count is what the audit reports. What did
not hold: "noventa dieciocho" split as 0 9 1 8 again, about half the time
across v14 and v15 despite the worked example. v16 does the arithmetic on
the server: `app/services/spanish_numerals.py` reads the Spanish number
words deterministically and hands the model a NUMERALS HEARD line of
`words = digits` pairs BESIDE the untouched utterance (a rewrite would
break the client's verbatim provenance floor); the prompt says take the
digits, quote her words. Gated to this call type and locale es. Also
v16: an identifier is echoed once per reply, with the letter on an
A-Number; on a long question the repeat is its short form.

## conf-v15 English: every v13 and v14 item held; the envelope break (v17)

English on v15 (81 turns, 127 facts): every v13 and v14 item held, the
read-back on the question and over after her yes, one guard hit (60).
The new break is the most important turn: on her yes to the Part 9
summary the whole read-back came back as PLAIN PROSE with no JSON
object (644 tokens, end_turn, not truncated), the harness could not
parse it, she resent her yes, and the lane reopened Part 11 and minted a
daytime phone it had said she did not have. On the phone that is an
error banner on the last step and a repeated section.

Two halves. Prompt (v17): every response is the object, the read-back
included, named as the last step; after a resend resume where you were
and never reopen a confirmed part; never note-to-verify and ask in the
same breath (17, 57); two asks per breath, no asides to third parties
(44, 48); a month and a year is a deferral, never a "2017-10" value
(38); a stated state is its own fact (32). Route: for this call type a
non-JSON body is retried ONCE with the same request plus a one-line
reminder; the first attempt stays metered with status envelope_retry; a
successful retry carries `envelope_retried: true` in the object; a retry
that is also prose is returned as-is with a warning log
(`n400_envelope_prose turn_id=...`), so the audit can count both.

Spanish v16 stamp (conf-es-v16, 24 turns): the numeral reader minted
627449018 first time with no correction, zero guard hits, zero stale
asking; the Spanish data item is closed. Five smalls into v17's
bilingual checklist: identifier once with the letter (6); no "termina en"
plus the whole number (21, 23); "la Parte 2", never "Part 2" in a Spanish
sentence (11, 23, 24); the first part's summary and object (6); a mumble
repeat never grows into "Le explico" (19); no status remarks about the
form (11).

## conf-v17 English: the envelope mechanism, and the contract flip (v18)

English on v17 (78 turns, 130 facts): zero guard hits, zero prose
failures reaching the client, the read-back as the object with over
after the yes, month-year dates all as deferrals. Five envelope retries
recovered, against one drop in 81 on v15. Read off the discarded
attempts in GP's usage log: four of the five CONTAIN the full object
after a paragraph of out-loud deliberation ("Looking at this: she
confirmed..."), the fifth is the same shape cut short. With thinking
disabled, v17's "decide FIRST, then..." framing plus the pre-OUTPUT
checklist invited deliberation in prose, and it landed in the output.
v18: the route EXTRACTS an object that follows a preamble (first brace
to last, parsed, `envelope_extracted: true`) and retries only when no
object is present at all; the prompt says the first character of the
response is the brace and the deciding happens inside the fields.

Contract flip, the auditor's to own: a STATED NONE on an optional field
(email, a second phone, a middle name, other names) is now a fact with
the empty string and her words as provenance; the client maps it to a
confirmed-empty marker and the agenda drops it. This reverses v13's
"never mint an empty value", which predated the client mapping.
Required fields are still never minted empty.

Also v18: "my only phone" mints the daytime as that number (61); the
note-then-ask defect was a CONFLICT between the DEFERRALS rule and the
hedged-year rule, now one rule keyed on whether the document is in hand
(47, 55); an agenda line with three or more ids is asked two per breath,
which tracked a five-id line (43, 45, 46, 60); the read-back walks every
part from Part 1 (77); the attorney sentence off the identity answer in
English too (2). The state from "Dallas Texas" (31) closed as the harness's: known facts
already carried p4.current_address.state: TX (derived from the
jurisdiction, as the app does), so the lane was right not to mint what
known facts answered.

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
