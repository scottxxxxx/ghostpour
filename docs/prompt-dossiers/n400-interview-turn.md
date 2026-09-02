---
call_type: n400_interview_turn
config_slug: n400/interview-turn
served_version: 3
model_dial: sonnet-5 (default only, no tier axis)
recommended_model: claude-sonnet-5
reconciled: 2026-09-01
---

# N-400 interview turn (n400_interview_turn)

## Intent

One conversational turn of a spoken intake interview for USCIS Form N-400.
Given the question that was asked, the fields it fills, what is already
known, and what the person said, produce a short reply, any facts that were
actually stated, and a clarification or conflict when either is warranted.
Output contract is the TurnResponse object in N-400's
`contracts/shared-data-model.md`.

**GP assembles this server-side.** An earlier draft of this dossier said
"phase 1 is client-assembled", which was wrong about this codebase and was
caught before merge: `interview-turn.json` is `server_only`, so `/v1/config`
404s it deliberately, and the client therefore cannot fetch it at all. The
lane is `_CALL_TYPE_TO_CONFIG` in `app/services/prompt_assembly.py`. The
client sends `call_type`, the answer and metadata; GP builds the message.
That IS "promptless".

For one PR review this file shipped with the config written and the
call_type NOT registered, which meant nothing read it: the client could not
fetch it and the server would not assemble it, and `assemble_prompt`
returning None leaves a request with NO system prompt at all. A prompt
config nothing is mapped to is a file, not a lane, and
`test_the_call_type_is_registered_or_nothing_reads_this_file` exists so that
cannot recur quietly.

## What binds this prompt

Scott's interview-style rulings, 2026-09-01, relayed through N-400 and
recorded in their `gp-ask.md` section 2:

- Converse like a human doing intake. Never march the field list one
  question at a time.
- NAME as one open ask. Three words is first, middle, last; two words is
  first and last, and only then ask about a legal middle name. Never record
  "N/A" as a middle name.
- ADDRESS as one open ask, then follow up on the gaps only.
- Summarize and confirm at the end of every section before moving on.

Two standing duties from their contract-asks ledger sit underneath the
style: never ask what the evidence already answers (18 or over is derivable
from date of birth), and infer then confirm rather than demanding exact
words.

## ⭐ A missing component is never completed from context

The load-bearing rule, and the reason this prompt is longer than it looks
like it needs to be.

Scott's on-device trial produced two real transcriptions. The full capture
was `April 12th, 1987`: ordinal suffix, comma, month written out. The other
was `12, 1987.`, the same spoken answer with the front cut off because the
recognizer opened a second late.

The second one is the dangerous shape. It looks like a complete short
answer. Completing it to April would be inventing a month on a federal
immigration form, and the model will do that willingly, silently and
confidently, because a birthday question makes April feel obvious. Their
client cannot fabricate it (every non-numeric branch of their date parse
requires an actual month word) and they pinned the property with
`truncatedDateNeverGainsAMissingComponent`. This prompt carries the same
rule on our side, generalized past dates: a street with no state is not
completed from the jurisdiction, a two-word name is not given a middle
name, a bare year is not given a January.

GP owes the stronger form of the ask and it is IN this prompt: echo the
components that were actually heard ("I heard the 12th of 1987, which
month?") rather than a generic re-ask. Their current client-side clarify is
the blunt version and they have queued the change.

## Deliberate omissions

**`fewShots` ships EMPTY, on purpose.** Two real utterances exist. That is
not a corpus. Few-shot examples written from what we imagine transcribed
speech looks like would teach the model one narrow idea of speech, which is
the same failure as the hand-written date pattern that made the interview
re-ask forever: correct-looking, confident, and wrong about the input. Fill
this from a real `provenance.utterance` export off the device after Scott
retries on the fixed build, then bump the version and add a shaping entry.

**No server-side parsing.** Nothing in GP parses dates, names or addresses
for N-400. Extraction lives in the model's structured output, which
SIDESTEPS the recognizer-shape problem rather than solving it. That is
worth stating rather than letting it read as robustness: a model is broadly
tolerant of shapes a regex rejects, and it is also capable of confidently
inventing the component the regex would simply have missed. The
never-complete rule is what covers the second failure, and it is a prompt
instruction, which means it is not enforced. Enforcing it server-side, by
rejecting a fact whose value carries a component absent from its own
`utterance`, is the obvious next instrument and is NOT built.

## Boundaries

The prompt may explain what a form question literally means, using what
USCIS publishes. It must never recommend an answer, say whether an answer
helps or hurts, interpret how law applies, decide eligibility, or assess
good moral character. Asked for any of those it names an attorney and
carries on with the interview rather than abandoning the turn.

Those same capabilities are enforced independently by the policy engine
(#849, `app/services/n400_policy.py`), which is the point: a prompt
instruction is a request and the matrix is a gate. `recommend_answer` is
ESCALATE there and `interpret_legally`, `determine_eligibility` are BLOCK
everywhere. If the two ever disagree, the matrix wins and the prompt is the
bug.

## Variables, and failing closed

The template takes `{{user_input}}` (the answer, `answer.final_text`) plus
named values read from request metadata: `form_code`, `jurisdiction`,
`locale`, `turn_id`, `section_label`, `question_text`, `field_ids`,
`known_facts`, `section_end_instruction`.

`requiredVariables` lists the ones a turn cannot be assembled without, and a
missing one is a **422 `missing_prompt_variables`**, not a warning. Before
this, an unsupplied placeholder produced a log line and a prompt carrying
the literal text `{{known_facts}}` to the model, which reads as a
well-formed request everywhere except in the answer. Only configs that
declare `requiredVariables` can raise, so every prompt that predates the
change behaves exactly as before.

`section_end_instruction` is deliberately NOT required: it is empty except
at a section boundary, and requiring it would refuse most turns.

⚠ **N-400 must be told which metadata keys to send.** Their `gp-ask.md`
declares `{case_id, node_id, jurisdiction, form_code}`; this needs more than
that. Until they send them, every turn 422s, which is the correct failure
(loud, named, and pointing at the missing keys) but it is a two-sided
change and it has NOT been relayed as of writing.

## Jurisdiction variants

`jurisdictions` maps a jurisdiction string to a partial config that
overrides fields for callers there, so one call_type serves a different
prompt by location while the app keeps calling one endpoint (Scott's ask,
2026-09-01).

Deliberately the same mechanism as `modes` rather than a new one, and
deliberately inside one document rather than a file per location: one
document is what lets the dashboard config editor show every variant side
by side, and it is what stops a file per state per language from drifting
apart silently. Composition order is modes, then jurisdictions, so a
location can override a surface-specific prompt. That order is not
arbitrary: the mode says what is being asked, the jurisdiction says what we
are permitted to say there, and the second is the one that must not be
overridable.

An absent or unrecognised jurisdiction inherits the base prompt. That is the
safe direction: a location nobody has written a variant for gets the general
prompt rather than nothing.

The map ships EMPTY and that is not a placeholder awaiting content. The base
prompt is correct everywhere until a location needs different words; the key
is present so the variant point is discoverable in the editor.

## Wire notes

- Feed `answer.final_text`, NOT `answer.transcript`. `final_text` is what
  the user saw and had the chance to edit, and their edit is deliberately
  not silent. Extracting from the raw transcript would read words the
  person already corrected.
- `reply` carries the requested locale, plus `en` when the locale is not
  `en`, matching the TurnResponse example in their data model.
- Locale variants of this file (`.es`, `.pt`) are NOT shipped. The prompt is
  authored in English and instructs the reply language, because the rules
  are what needs to be precise and translating them three ways triples the
  surface where a rule can drift. Revisit if reply quality in es or pt is
  measurably worse.
- No em dashes or en dashes anywhere, in this file or in the served prompt.
  The model copies the punctuation it sees.

## Shaping history

- 2026-09-01 (#852, v2): registered the call_type, so the config is read at
  all; named prompt variables with a fail-closed `requiredVariables` guard;
  added the `jurisdictions` variant axis. Also corrected this dossier's
  claim that assembly was client-side, which it never was here.
- 2026-09-01 (#852, v1): first version. Authored from N-400's `gp-ask.md` §2,
  the TurnResponse contract, Scott's four intake rulings, and the two real
  transcriptions from his device trial. `fewShots` empty pending a real
  export. No eval has been run: there is nothing honest to evaluate against
  until real utterances exist.

## Tuning rules

- Never fill `fewShots` with invented speech. The corpus comes off a device
  or it does not come.
- The never-complete rule is the highest-priority instruction in the
  prompt. If a future edit shortens the prompt, that section is the last
  thing to go, not the first.
- Model dial is `default` only. N-400 has no per-call entitlement reaching
  GP, so a free/paid split here would look configured and never be read.
