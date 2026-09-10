# STE prompt lint: three A/B samples for evaluation

Written 2026-09-10. **Nothing here is served.** No config file was changed.
These are candidate rewrites, produced so the effect can be judged before any
prompt moves.

The tool is `scripts/prompt_ste_lint.py`, tested by
`tests/test_prompt_ste_lint.py`. It implements the checkable writing rules of
ASD-STE100 in our own words. The 900 word dictionary is copyrighted and is not
reproduced, so a clean score is NOT a claim of STE compliance.

## Why bother, in one paragraph

Across the served corpus: 25,659 words, 1,376 sentences, 446 flagged (32%), and
331 prohibitions against 164 positive instructions. The N-400 interviewer turn
v29 is the outlier at 28.2 mean words per sentence, 118 semicolons, 470 all caps
tokens, 187 "never" against 3 "always", and one period to period stretch of 363
words carrying about 21 separate rules. Two defects this project already paid
for point the same way: a prompt rule in that lane banned the mechanism it was
written to protect, and when the per-field judge went unstable across reps the
instability tracked specification ambiguity rather than sampling noise. A rule
that shares a sentence with four other rules cannot be tested on its own.

**What the lint cannot do.** It cannot tell you a rule is wrong. A twelve word
sentence stating a false rule scores perfectly. Read a flag as "go look at this
line", never as "this line is broken".

---

## Sample A: ShoulderSurf `summaryPrompts.full`

The whole served prompt, so the register is judgeable and not just the syntax.

### Before, as served in v26

```
You are a meeting summarizer. Provide a concise summary of the key points discussed so far. Prioritize: decisions made (with who decided), action items (with owners and deadlines if stated), open questions. Be terse: bullet points only. Group by topic if the meeting covered multiple subjects. Never use em dashes or en dashes anywhere in your output. Where one would fit, use a comma, colon, or parentheses instead. Use only names and facts that appear in the transcript. Refer to people, pets, and organizations exactly as the transcript names them; when no full name is given, keep the name as given and never invent surnames or details. LANGUAGE: write in the language the participants speak in the transcript, whatever language these instructions are written in. If part of the transcript is noisy, partial or mistranscribed, summarize everything that can be understood from the rest and note the garbled stretch in one short sentence in that language. Never declare the whole transcript unusable: a meeting whose opening minutes are garbled still has a summary in the minutes that are clear. Never refuse, and never ask for a transcript in another language.
```

### After

```
You are a meeting summarizer.
Write a concise summary of the key points so far.
Cover these three things, in this order: decisions, action items, open questions.
Name who made each decision.
Name the owner of each action item.
Add a deadline to an action item only when the transcript states one.
Write bullet points only.
Group the bullets by topic when the meeting covered more than one subject.
Use a comma, a colon or parentheses in place of any dash.
Never write an em dash or an en dash.
Use only the names and facts that appear in the transcript.
Name every person, pet and organization exactly as the transcript names them.
Keep a partial name as the transcript gives it.
Never invent a surname.
Never invent a detail.
LANGUAGE. Write the summary in the language the participants speak in the transcript.
This rule holds whatever language these instructions use.
Summarize every part of the transcript you can understand.
Note each noisy, partial or mistranscribed stretch in one short sentence.
Write that sentence in the same language as the summary.
Summarize the clear minutes even when the opening minutes are garbled.
Never call a whole transcript unusable.
Never refuse.
Never ask for a transcript in another language.
```

| | before | after |
|---|---|---|
| sentences | 13 | 25 |
| words | 190 | 208 |
| mean sentence words | 14.6 | 8.3 |
| flagged sentences % | 38.5 | 4.0 |
| prohibitions | 4 | 6 |
| positive instructions | 3 | 15 |
| prohibition ratio | 1.33 | 0.4 |
| long_descriptive | 1 | 0 |
| long_instruction | 2 | 0 |
| multi_instruction | 2 | 0 |
| passive | 2 | 1 |

**The one residual flag is the tool's fault.** "even when the opening minutes
are garbled" reads as passive to the regex and is a predicate adjective. It is
left in rather than added to the exception list, because tuning the instrument
to flatter this rewrite would end its usefulness.

### Three rulings this rewrite had to make, which is the real finding

The original was ambiguous in three places, and any rewrite has to pick a
reading. Each of these is yours or the auditor's to confirm, not mine:

1. **"note the garbled stretch in one short sentence in that language".** Which
   language? The nearest noun is the transcript, the intended antecedent is
   probably the summary's language, and since #956 those two can differ, because
   an English phone now gets an English summary of a Spanish meeting. The
   rewrite says "the same language as the summary". If you want the note in the
   TRANSCRIPT's language instead, that is a different line.

2. **"action items (with owners and deadlines if stated)".** Does "if stated"
   govern owners as well as deadlines? The rewrite reads it as deadlines only,
   so an action item with no named owner still gets listed. The other reading
   drops ownerless action items entirely.

3. **"Never declare the whole transcript unusable: a meeting whose opening
   minutes are garbled still has a summary in the minutes that are clear".** The
   colon carried the rationale. The rewrite splits the instruction from the
   reason, which means the reason is now droppable without touching the rule.

One more thing worth your eye, not a rewrite decision. `app/services/
language_directive.py` APPENDS its directive after this prompt when the client
states a language, so the model sees both the base rule and the directive. They
agree in the ordinary case. The appended line carries an exception the base
prompt does not have ("unless the user writes to you in a different language"),
so that exception disappears whenever no language tag arrives. I read the file;
I am not claiming it is a defect.

---

## Sample B: N-400 v29, the spoken word caps

### Before, verbatim

```
These are the SPOKEN WORD CAPS, and they are counted, not felt: an ordinary question turn 35 words (42 in Spanish, which needs more words for the same thing), a section summary 60, a Part 9 battery 75 because it must name every item it mints, the opening 45.
```

### After

```
SPOKEN WORD CAPS. Count the words of every turn you speak.
An ordinary question turn: 35 words.
An ordinary question turn in Spanish: 42 words.
The opening: 45 words.
A section summary: 60 words.
A Part 9 battery: 75 words, because a battery names every item it mints.
```

| | before | after |
|---|---|---|
| sentences | 1 | 7 |
| words | 49 | 48 |
| mean sentence words | 49.0 | 6.9 |
| flagged sentences % | 100.0 | 0.0 |
| prohibitions | 0 | 0 |
| positive instructions | 0 | 0 |
| prohibition ratio | None | None |
| long_descriptive | 1 | 0 |
| open_pronoun | 1 | 0 |
| passive | 1 | 0 |

Five caps in one 49 word sentence become five lines. The point is not the score:
it is that each cap can now carry its own test, and the Spanish exception stops
living inside a parenthesis inside a clause.

---

## Sample C: N-400 v29, the first six rules of the 363 word chain

Six of roughly 21 rules that share one period to period stretch.

### Before, verbatim

```
These hold in every language, and they are the ones that slip: "are you a person?" gets "an assistant, not a person" and NOTHING about attorneys, in English too; the opening never asks what APPLICANT CONTEXT already answers (an interpreter it says they will not use); the opening and every reply end on ONE question, never two bundled; until gender is known use forms that do not inflect ("por su cuenta", "si no tiene certeza"); an identifier you took is read back, whole or last four, never silently "noted"; `asking` is the node of the NEXT question, never a node a fact in this response fills, and null on a checkpoint reply (el nodo de la próxima pregunta, nunca el que acaba de responderse);
```

### After

```
Answer "are you a person?" with "an assistant, not a person".
Say nothing about attorneys in that answer.
This rule holds in English too.
The opening never asks for what APPLICANT CONTEXT already answers.
Example: an interpreter that APPLICANT CONTEXT says the applicant will not use.
End the opening on one question.
End every reply on one question.
Never bundle two questions into one turn.
Use forms that do not inflect until you know the applicant's gender.
Spanish examples: "por su cuenta", "si no tiene certeza".
Read every identifier you take back to the applicant, whole or last four digits.
Never record an identifier as "noted" without reading it back.
Set `asking` to the node of the NEXT question.
Never set `asking` to a node that a fact in this response already fills.
Set `asking` to null on a checkpoint reply.
```

| | before | after |
|---|---|---|
| sentences | 1 | 15 |
| words | 124 | 141 |
| mean sentence words | 124.0 | 9.4 |
| flagged sentences % | 100.0 | 0.0 |
| prohibitions | 1 | 5 |
| positive instructions | 0 | 7 |
| prohibition ratio | None | 0.71 |
| long_descriptive | 1 | 0 |

**An ambiguity preserved on purpose.** "an identifier you took is read back,
whole or last four, never silently noted" does not say WHO chooses between the
whole identifier and the last four, or when. The rewrite keeps both options and
does not invent a rule. That gap is now visible on its own line instead of
buried in the fourteenth clause of a chain.

---

## How to run it

```
python3 scripts/prompt_ste_lint.py                      # human report
python3 scripts/prompt_ste_lint.py --show 5             # with example lines
python3 scripts/prompt_ste_lint.py --json before.json   # snapshot
python3 scripts/prompt_ste_lint.py --baseline before.json   # exit 1 if worse
```

The baseline gate compares the prohibition RATIO, never the raw count. Splitting
"never do X, and never do Y" into two sentences raises the count while making
the prompt strictly more testable, and the first version of this gate would have
failed exactly the edit it exists to encourage. That bug was found by running
the tool on sample A.

## What has NOT been done

- No served prompt has been changed, and no config version was bumped.
- The N-400 rewrite has not been scored by the per-field judge. Until it is,
  "this reads better" is an aesthetic claim, and the lane has a validated
  instrument that could make it an empirical one.
- fable-auditor-55 directs the N-400 lane and has not seen these samples.
