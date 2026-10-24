# TR to GP: `tr_compare_reality` v2, per question cards for the one Review

2026-10-23, from the Tech Rehearsal iOS team. **Status: PROPOSED.** Nothing
is built on either side. Standing process: this contract, then GP config,
then the promptless flip, then harness verification with a heads-up.

This is an additive revision of the v1 contract in
`tr-compare-reality-contract.md` (live since 2026-07-10, with the
calibration amendment: silence produces no section, recap absence is not
evidence). Every new key is optional. Shipped v1 clients keep working
against a v2 config because they ignore keys they don't know, and TR's
decoder is already fail-soft on every field. Config today: version 10,
`maxTokens` 8192, temperature 0.2, Sonnet lane in `model-routing.json`.

## Why

TR's design team settled the one Review screen on 2026-10-23 (navigation
canvas row 9). It renders one card per interview question. Once a real
conversation exists, each card's chip becomes LANDED, DRIFTED or NEW (a
question that was asked but the user never rehearsed), with two wells side
by side: YOU REHEARSED (the user's best practice answer, TR has that
locally) beside YOU SAID (from the transcript). Under the wells: What
drifted, What held, Make it stronger. The summary shows "N landed, N
drifted, N new question", and a new question gets the line "It's added to
your next practice round automatically."

Recap captures (the user's own account, no recording) need the same cards
with the wells relabeled PLANNED TO SAY and AS YOU DESCRIBED IT.

Today's compare output is keyed by a 2 to 4 word `topic` with `planned`,
`reality`, `delta` (landed | drifted | missed | unplanned) and one `note`,
plus flat `landed` and `drifted` string lists. Three things the card needs
that the output can't give:

1. A reliable join from a section to a practice question. Matching a topic
   like "Conflict story" to the practiced question text is a heuristic, and
   when it misfires the user's rehearsed answer lands beside the wrong real
   answer, which is worse than showing nothing.
2. A rating for the real answer, on the same Weak / Meets / Strong / Bar
   Raiser scale the practice cards already use, so PREP and LIVE read on one
   scale.
3. An explicit "new question" flag. `unplanned` is close, but it was written
   against the plan's key points, not against the practiced question list,
   and TR needs the question text to seed the next round.

## What stays the same

- Envelope: `call_type: "tr_compare_reality"`, `provider/model: auto/auto`,
  `context_quilt: false`, `locale`, `metadata.scenario`,
  `metadata.scenario_kind`, `metadata.capture` (`transcript` | `recap`). No
  `system_prompt`, ever.
- Every v1 blob section, with the same labels, in the same order.
- Every v1 output key, with the same meaning and the same required set
  (`verdict` and `next_best_focus` required; `landed` and `drifted` may be
  empty; a reply with no `sections` and no `verdict` is a miss).
- All of the v1 calibration, including the 2026-07-10 amendment and the
  2026-08-07 tightening: `missed` is strictly an absence, a fumble is
  `drifted`.
- Temperature 0.2 and the routing lane. The transcript is still the long
  part of the input; the extra output is small per section.

## Input change: the PRACTICE ANALYSIS block gains a numbered question list

So the model can join, the PRACTICE ANALYSIS block gains a numbered list of
the questions the user practiced, each with its rating and a short gist of
the answer. The rest of the v1 blob is unchanged.

```
PRACTICE ANALYSIS (best round: Round 2 of 3, scored 71/100):
OVERALL: 71/100, Solid stories, thin on metrics
BIGGEST GAP: Quantified results, two of six answers had no number in them
COACHING GIVEN:
- Name the metric you moved and by how much
- Say what you would do differently, not just what happened

QUESTIONS PRACTICED (best round):
1. [Ownership] Q: Tell me about a time you took on something outside your role. | RATING: Strong | A: Took over the on-call rotation redesign when the lead left; cut pages 40 percent in a quarter and wrote the runbook the team still uses.
2. [Conflict] Q: Describe a disagreement with a peer and how you resolved it. | RATING: Meets | A: Disagreed with a PM over scope; set up a data review, we split the feature, shipped the smaller half first.
3. [System design] Q: How would you design a rate limiter for a public API? | RATING: Weak | A: Token bucket per key in Redis; did not cover multi-region or what happens when Redis is down.
4. [Failure] Q: Tell me about a project that failed. | RATING: Meets | A: A migration that slipped two quarters; blamed vendor timelines, did not say what I would change.
```

Line format, one line per question, in the order the round asked them:

```
<index>. [<dimension or type>] Q: <question text> | RATING: <Weak | Meets | Strong | Bar Raiser> | A: <one or two sentence gist of the user's best answer>
```

- `<index>` is 1-based and is what `practice_index` points at.
- `[<dimension or type>]` is TR's `competency` when the round recorded one,
  else the question `type`. It is a hint for matching, not a key.
- `RATING` is the practice rating TR already has. On non-interview
  scenarios (negotiation, personal, pitch) the practice turns carry no STAR
  rating, so the `RATING:` segment is omitted from the line.
- `A:` is the user's own best answer, clipped to about 300 characters at a
  sentence boundary. It is a gist for matching and for the "what held"
  judgment, not the full transcript of the answer; TR renders the full
  answer in the YOU REHEARSED well from local data.
- The block carries at most 15 entries. Rounds run 5 to 10 questions, so
  this is a guard, not a cap anyone should hit.

**Several practice rounds.** TR sends exactly one round: the best round by
score (max `displayScore` across the conversation's mocks, same rule the
Review's "Best practice round" tile uses). The header names it, "Round 2 of
3, scored 71/100", so the verdict can refer to it. Questions are never
merged across rounds; one round means `practice_index` is unambiguous.

**One round.** Same format, "Round 1 of 1".

**No practice round.** No PRACTICE ANALYSIS block at all, exactly as today.
Then there is no QUESTIONS PRACTICED list, `practice_index` is null on every
section, and the v1 rule applies: compare against the REHEARSAL PLAN alone
and say so in the verdict. `new` in that case keeps the v1 `unplanned`
meaning (came up, and nothing in the plan prepared for it). TR renders
those cards without the YOU REHEARSED well.

## Output change: five optional keys per section, `new`, and `counts`

```json
{
  "contract": 2,
  "verdict": "one or two sentences: how reality compared to what was rehearsed",
  "counts": { "landed": 1, "drifted": 1, "new": 1 },
  "sections": [
    {
      "topic": "2-4 word noun phrase (unchanged, TR's fallback join key)",
      "question": "the question as actually asked, verbatim or a close paraphrase; for a missed section, the planned question",
      "practice_index": 1,
      "delta": "landed | drifted | missed | new (unplanned accepted as an alias of new)",
      "rating": "Strong | Meets | Weak | Bar Raiser for the REAL answer, interview scenarios only, else null",
      "planned": "what the rehearsal prepared for this (unchanged)",
      "reality": "what actually happened (unchanged)",
      "held": "what still came through from the rehearsal, one sentence",
      "fix": "one concrete fix for the next round, one sentence",
      "note": "one coaching-relevant sentence (unchanged)"
    }
  ],
  "landed": ["unchanged"],
  "drifted": ["unchanged"],
  "coaching": ["unchanged"],
  "next_best_focus": "unchanged, required"
}
```

Per field:

- `question` (string, optional). For `landed`, `drifted` and `new`: the
  question as the counterpart actually asked it, verbatim from the
  transcript or a close paraphrase when the transcript garbled it. For
  `missed`: the planned question, copied from QUESTIONS PRACTICED. This is
  the text TR shows at the top of the card and the text it seeds into the
  next practice round for a `new` question, so it has to read as a
  question, not a topic label.
- `practice_index` (integer or null, optional). 1-based into QUESTIONS
  PRACTICED. Must point at a real entry or be null. Never guessed: a wrong
  index puts the user's rehearsed answer beside the wrong real answer on
  the card. Null on every `new` section, null everywhere when there is no
  QUESTIONS PRACTICED block. Two real questions can legitimately point at
  the same entry (the interviewer asked it twice in different words); when
  one real question covers two practiced entries, pick the closest and name
  the other in `note`.
- `rating` (string or null, optional). The rating of the REAL answer on the
  practice scale, interview scenarios only. Null for non-interview
  scenarios, null on `missed` (nothing was delivered), and null on a recap
  unless the user quotes their own answer at length (see evidence rules).
  Null is a normal value here, not a failure.
- `held` (string, optional). What still came through from the rehearsal.
  On `landed` this is the substance of the card. On `drifted`, the part
  that survived. On `new`, what the user did well unrehearsed. On `missed`,
  omit it or null.
- `fix` (string, optional). One concrete fix for the next round, in the
  second person, something the user can rehearse. Expected on every
  `drifted` and `new` section. Optional on `landed` (what would take a
  Strong to a Bar Raiser). Omit on `missed`.
- `delta` gains `new` as the preferred value for a question that was asked
  and never rehearsed. `unplanned` stays accepted as an alias; TR
  normalizes it to `new` on decode. `landed`, `drifted`, `missed` are
  unchanged, including the rule that a fumble is `drifted`.
- `note` is unchanged and still expected.
- `topic` is unchanged and still expected. It is TR's fallback join key
  when `practice_index` is null and the v1 renderer's label.

Top level:

- `counts` (object, optional): `{ "landed": n, "drifted": n, "new": n }`,
  computed from `sections` by delta. `missed` is left out on purpose; the
  summary line doesn't show it. The point is that TR and GP agree on the
  three numbers the user sees, and that a mismatch is a tripwire (a section
  counted that was never emitted would mean the response was cut).
- `contract` (integer, optional): `2` when the response was produced by the
  v2 config. See open question 3.

`held`, `fix`, `question` and `note` follow the same output rules as the
rest of the prompt: no em or en dashes, no spaced hyphens, grounded in what
was said or reported, never invented.

## Evidence rules per capture mode (extending the v1 calibration)

The v1 rules stand: a RECORDED TRANSCRIPT makes absence evidence, a USER
RECAP does not, and silence produces no section in either mode. The new
fields need the same treatment, because each one is a new way to turn
silence into a verdict.

On a RECORDED TRANSCRIPT:

- A section is `new` only when the question is clearly asked in the
  transcript and matches nothing in QUESTIONS PRACTICED by meaning. Match
  by meaning, not by words: "walk me through a time you pushed back on a
  peer" is the practiced Conflict question, not a new one. A follow-up
  probe inside a practiced question's answer is part of that question, not
  a new section.
- `rating` is judged from what the transcript shows the user said. The v1
  input note applies: transcription artifacts (homophones, mangled proper
  nouns, dropped small words) never lower a rating.
- `question` comes from the transcript's wording.
- `practice_index` is set when the match is clear and null otherwise. Null
  beats wrong.
- `missed` still applies as in v1: a practiced question that never appears
  is missed, and its `question` is the planned text.

On a USER RECAP:

- A section is `new` only when the user's own account says they were asked
  something they had not prepared ("they asked about X, which I hadn't
  thought about"). A recap that simply doesn't mention a practiced question
  gets no section for it (v1 amendment), and a recap that mentions a topic
  without saying it was unprepared is not evidence of `new`.
- `rating` is null unless the user quotes their own answer at length: a few
  sentences of what they actually said, not "I talked about the migration".
  A rating on a paraphrase would be a grade on the user's memory of the
  answer, not the answer.
- `held` and `fix` are anchored to what the user reports, in the same
  charitable register as `note`. They never penalize the brevity of the
  recap, only what it reveals.
- `practice_index` is set only when the account names the question or its
  topic clearly enough that the join is not a guess.
- `question` is the user's own phrasing of what they were asked, or the
  planned question text when the user clearly refers to a practiced one.

Both modes:

- Silence produces no section. A practiced question the capture says
  nothing about gets no card; its reminder folds into `coaching`, as in v1.
- A null `rating` or a null `practice_index` is the honest state, not a
  gap to fill. The v1 lesson still holds: a closed field with no "unknown"
  value gets force-fit, so every new field here has one.
- `counts` is derived from the sections actually emitted, never from what
  the model intended to emit.

## Example: transcript capture, interview

Input tail (the QUESTIONS PRACTICED block above, then):

```
REAL CONVERSATION (RECORDED TRANSCRIPT):
Interviewer: Tell me about a time you owned something that wasn't yours to own.
Candidate: Sure. When our lead left mid quarter the on call rotation was a mess ...
  (the user gives the rotation story, with the 40 percent figure)
Interviewer: Can you talk about a time you and a colleague disagreed?
Candidate: Yeah, so a PM and I disagreed about scope ... we ended up splitting it ...
Interviewer: And what was the result of shipping the smaller half first?
Candidate: It shipped. Then the second half went out later.
Interviewer: Last one. How do you decide when to deprecate a public API?
Candidate: Honestly I haven't driven one of those. I would look at usage first ...
```

Output:

```json
{
  "contract": 2,
  "verdict": "Two of your four practiced questions came up and the ownership story landed exactly as rehearsed. The conflict story lost its result under a follow-up, and the API deprecation question was new ground you handled candidly but thinly.",
  "counts": { "landed": 1, "drifted": 1, "new": 1 },
  "sections": [
    {
      "topic": "Ownership story",
      "question": "Tell me about a time you owned something that wasn't yours to own.",
      "practice_index": 1,
      "delta": "landed",
      "rating": "Strong",
      "planned": "The on-call rotation redesign after the lead left, with the 40 percent drop in pages and the runbook.",
      "reality": "You told the rotation story in order, named the 40 percent figure, and closed on the runbook still being in use.",
      "held": "The situation, the number and the lasting artifact all came through in the same order you rehearsed.",
      "fix": "Add one line on what you would do differently next time; that is the beat that moves this to Bar Raiser.",
      "note": "Delivered as rehearsed, with the metric intact."
    },
    {
      "topic": "Conflict resolution",
      "question": "Can you talk about a time you and a colleague disagreed?",
      "practice_index": 2,
      "delta": "drifted",
      "rating": "Meets",
      "planned": "The scope disagreement with the PM, the data review, and shipping the smaller half first.",
      "reality": "You set up the disagreement and the split, but when asked for the result you said only that it shipped.",
      "held": "The setup and the data review landed, and you kept the tone about the work, not the person.",
      "fix": "Rehearse the result as a number, what shipping the smaller half first bought you (adoption, time, a decision), so a follow-up on outcome has somewhere to go.",
      "note": "The follow-up on outcome is where this drifted; the rehearsed answer had no result either, so this was a practice gap, not nerves."
    },
    {
      "topic": "API deprecation",
      "question": "How do you decide when to deprecate a public API?",
      "practice_index": null,
      "delta": "new",
      "rating": "Weak",
      "planned": "Not rehearsed.",
      "reality": "You said you had not driven one and reasoned from usage data, without reaching migration paths, timelines or communication.",
      "held": "You were honest about the gap and started from the right signal, usage.",
      "fix": "Prepare a four-step deprecation answer: usage audit, migration path, announced timeline with a sunset date, and how you handle the last holdouts.",
      "note": "New question. Candor was the right call, and this is now the one to practice before the next round."
    }
  ],
  "landed": ["The ownership story, with the 40 percent figure and the runbook"],
  "drifted": ["The conflict story had no measurable result when the interviewer asked for one"],
  "coaching": [
    "Add a measured result to the conflict story before the next round.",
    "Build the API deprecation answer; it is now in your next practice round.",
    "Your system design and failure questions did not come up this time; keep them warm."
  ],
  "next_best_focus": "A one-sentence measurable result at the end of every story."
}
```

The System design and Failure questions (practice entries 3 and 4) were
never asked, so under the v1 transcript rule they are `missed`. They are
left out of this example only to keep it to three sections; a real response
would carry them as `missed` sections with `practice_index` 3 and 4,
`rating` null, no `held` or `fix`, and `question` set to the planned text.

## Example: recap capture, interview

Input tail (same QUESTIONS PRACTICED block, then):

```
REAL CONVERSATION (USER RECAP, first-person, self-reported, unverified):
It went okay. She opened with the ownership question and I gave the on call
story pretty much the way I practiced it, got the 40 percent in. Then she
asked how I decide when to kill a public API, which I had not prepped at
all. I said something like: "I'd start with usage, see who is still calling
it, then give them a migration path and a date. I'd rather over communicate
a sunset than surprise anyone, and I'd keep a shim up for the last few
callers for a while." She seemed fine with it. We ran out of time after
that.
```

Output:

```json
{
  "contract": 2,
  "verdict": "By your account the ownership story landed as practiced, and the one question you had not prepared, API deprecation, you answered with a reasonable structure on the spot. Only two of your four practiced questions came up before time ran out.",
  "counts": { "landed": 1, "drifted": 0, "new": 1 },
  "sections": [
    {
      "topic": "Ownership story",
      "question": "Tell me about a time you took on something outside your role.",
      "practice_index": 1,
      "delta": "landed",
      "rating": null,
      "planned": "The on-call rotation redesign with the 40 percent drop in pages and the runbook.",
      "reality": "You report telling it the way you practiced and getting the 40 percent figure in.",
      "held": "By your account the story and its number came through.",
      "fix": null,
      "note": "Matches the plan as you describe it; no rating without the answer itself."
    },
    {
      "topic": "API deprecation",
      "question": "How do you decide when to kill a public API?",
      "practice_index": null,
      "delta": "new",
      "rating": "Meets",
      "planned": "Not rehearsed.",
      "reality": "You quote a four-part answer: usage first, a migration path and a date, over-communicating the sunset, and a shim for the last callers.",
      "held": "The structure was sound and you reached communication and the long tail, which most unrehearsed answers skip.",
      "fix": "Add one real example of a deprecation you were near, even as an observer, and one number (callers, months) so it reads as experience rather than reasoning.",
      "note": "New question, rated because you quoted your answer; the quote is what makes a rating fair here."
    }
  ],
  "landed": ["The ownership story, as you describe it"],
  "drifted": [],
  "coaching": [
    "The API deprecation question is now in your next practice round; give it a real example.",
    "Your conflict, system design and failure questions did not come up by your account; keep them ready.",
    "Next time, note what each question was and roughly what you said, so the comparison can rate more of your answers."
  ],
  "next_best_focus": "A real example for the API deprecation answer."
}
```

Things to notice: the first section has `rating: null` because the user
said "pretty much the way I practiced it" without quoting the answer; the
second has a rating because the user quoted it at length; `drifted` is
empty because nothing drifted, which v1 already allowed; the three
practiced questions the recap never mentions get no section (recap absence
is not evidence) and fold into coaching; and the thin-recap coaching line is
there because two of four is a thin basis.

## TR client plan (gated on GP config)

Nothing ships until GP confirms the v2 config is live. The blob change and
the decoder change ship together behind that confirmation, so the live v1
prompt never sees the QUESTIONS PRACTICED block.

- `CompareRealityClient.buildUserContent` gains the best-round header and
  the QUESTIONS PRACTICED list, built from the best round's
  `MockReport.questions` (`competency` or `type`, `question`, `rating`,
  `answer` clipped to a sentence boundary near 300 characters). The best
  round is max `displayScore` across the conversation's mocks.
- `WireSection` gains optional `question`, `practice_index`, `rating`,
  `held`, `fix`; `Wire` gains optional `counts` and `contract`. Decode is
  fail-soft on every one. `unplanned` is normalized to `new`.
  `CompareReport.Section` grows the same optionals, so reports persisted on
  the `MeetingRecord` under v1 still decode.
- Join: by `practice_index` into the best round's questions when present
  and in range. Otherwise fall back to the v1 topic heuristic (fuzzy match
  of `topic` against question text and competency). When neither matches,
  the card renders without the YOU REHEARSED well rather than beside a
  guessed answer.
- Card chip: `rating` when present, else the delta chip alone. Counts: GP's
  `counts` when present, else counted client-side from sections; a mismatch
  is logged in DEBUG builds.
- New questions: every `new` section with a non-empty `question` is
  appended to the next practice round's question set, which is what the
  "It's added to your next practice round automatically" line promises.
- Harness: `tools/gp-harness` gets a v2 fixture set and `_compare_body`
  mirrors the new block (wire-body changes in the client must be mirrored
  in the harness builders). Checks: `practice_index` null or in range,
  delta in the five accepted values, `counts` equals the section tally, a
  rating only on interview scenarios, a thin recap still yields no `new`
  and no `rating`, no dashes anywhere. Fired only after GP's heads-up.
- The prompt dossier (`docs/prompt-dossiers/compare-reality.md`) gets a
  shaping-history line when this lands; GP's call whether the
  `test_tr_compare_reality_delta.py` style guard grows a v2 sibling (we are
  happy to draft one).

## Open questions for GP

1. **Ceiling.** The compare call sits at `maxTokens` 8192 and was at 95
   percent of its cap in the 2026-08-18 sweep before the raise. v2 adds
   five short fields per section plus the question text, roughly 80 to 120
   tokens a section, so about 700 to 900 tokens on a seven-section
   response, more when `missed` sections carry planned question text. Does
   that need a bump to 12288, or do you want to measure first? Routing lane
   unchanged either way; this is the same analysis-grade work with a
   slightly longer answer.
2. **`counts`.** Worth having the model emit it, as a shared tripwire, or
   should TR just count client-side? Our lean is to keep it: it costs a
   dozen tokens and catches a truncated section list.
3. **Versioning.** Should the response carry `contract: 2` so TR can tell
   which shape it got, or would you rather signal it another way (a
   response header, or TR inferring from the presence of the new keys)? A
   field in the JSON is the simplest thing the model can do and the
   simplest thing TR can read; inference from key presence is fragile when
   a v2 response legitimately has every optional null.
4. **Rating off the interview rubric.** We propose `rating` null for
   non-interview scenarios in v2. If you would rather emit a facet word
   there (the five personal facets), say so and we will carry it, but the
   Review's rating chip is the STAR scale and anything else needs design
   work on our side.

The TR session is `techrehearsal-26`.
