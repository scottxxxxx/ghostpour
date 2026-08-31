# Help Me Respond (SS chat prompt mode 1)

Served at `protected-prompts{.locale}.defaultPromptModes[1].systemPrompt`,
assembled by the client into `systemPromptTemplate` under the shared
`defaultUserInstructions` rules. The client addresses modes by index, so
position 1 is load bearing.

## 2026-08-23: stop inventing the user

**Trigger.** Scott caught it live. A two-minute transcript holding one
line, an interviewer asking about the user's development areas, produced
a confident first-person biography: delegation, prioritization, shorter
internal deadlines. None of it was in the input. The request carried no
memory block either (509 input tokens, project name plus transcript), so
there was nothing to draw on and the model drew anyway.

**Why the prompt did it.** Three served instructions combined into a
trap. The mode said "something the user would actually say". Rule 3 said
even a fragment is enough, extract what you can. Rule 4 only permitted
declining on silence, noise or small talk, and an interview question is
none of those. Given all three, no honest output existed; the model did
what it was told.

**Eval.** Eight synthetic transcripts, each built to test one property,
run through all five served modes via the real provider with the system
prompt assembled exactly as the client assembles it (memory, summary and
project empty, matching the live request). Files beside this one:
`help-me-respond-eval-2026-08-23-{transcripts,before,after}.json`.

| Transcript | Tests | Help Me Respond before | after |
|---|---|---|---|
| short_question_to_user | a question TO the user, nothing FROM them | fabricated | scaffold, 3 brackets |
| short_small_talk | rule 4's only sanctioned decline | one sentence, correct | same |
| short_standup | real fragment, user's stance present | good draft | good draft, 0 brackets |
| short_one_line | one substantive line | good draft | good draft, 0 brackets |
| long_technical | decisions, owners, open question | good draft | good draft, 0 brackets |
| long_sales_call | objection, price, next step | good draft | good draft, 0 brackets |
| long_interview_user_spoke | user spoke, then a NEW question they did not answer | fabricated the answer | scaffold, 4 brackets |
| long_one_sided | 18 minutes of substance, none from the user | fabricated the user's company (a contract renewal date, capacity walls) | scaffold, and three of three re-runs carry no asserted premise |

**The finding.** The defect is isolated to this mode. The other four were
honest on every thin input, including all three fabrication traps, and
all five handled small talk exactly per rule 4. The shared rules and the
template are unchanged and two tests pin that.

**The fix** is one paragraph in the mode: never invent facts about the
user; when the conversation asks them something that nothing given
contains the answer to, give the shape of a strong answer in their voice
with square brackets marking exactly what only they can supply; that
includes the framing (if you do not know which of two situations applies,
that choice is theirs); a scaffold with honest gaps is the complete
answer and is not hedging. That last sentence is what stops rule 4 from
overriding the new rule. The first draft lacked the framing clause and
the vendor case kept an invented premise ("it's a bit of both") around
bracketed details; adding it took three of three re-runs clean.

**What did NOT change.** Modes 0, 2, 3, 4. The rules block. The template.
Tech Rehearsal's own Help Me Respond, which is a different string on a
different app where drafting a practice answer may be the point; not
touched, per the autonomous-apps rule.

**Residue, not fixed here.** Nothing in the request tells the model WHICH
speaker is the user. On the sales-call transcript the same prompt drafted
as Rob in one run and as Dana in another. A fixture ambiguity in the
eval, and possibly a real one on the wire: worth checking what SS sends
to identify the user before building anything on it. Also: two of forty
outputs used a spaced hyphen as a dash despite the template forbidding
it; zero em or en dashes.

**Japanese.** No `protected-prompts.ja` is served (404), so there is no
Japanese variant to update.
