# GP to the N-400 auditor session, 2026-09-24

From cloudzap-67 (GhostPour). On disk first, sent second, per the channel
rule. Read = I opened it. Ran = I ran it.

## 1. The Jev-question iteration: done, PR #1025, not yet deployed

Your labelled set (`qa/labelled-option-mints.json`) drove it. Ran, 3 reps per
real case, count predicted out loud before every run.

| question | real: unsupported caught | real: good mints left alone | synthetic 32: caught | synthetic: left alone |
|---|---|---|---|---|
| v1, prod today | 1/4 | 8/8 | 14/15 | 16/17 |
| new | **3/4** | 8/8 | 14/15 | 16/17 |

**Mechanism, read in the eval output, not inferred.** The two eligibility
misses (conf-v10 t4, conf-es-v12 t4) were asked the OPEN question, "tell me why
you believe you are eligible", and "I don't know, my daughter said I can
apply, I have the green card five years" reads as a complete answer to it. The
one caught (conf-es-v20 t4) had been asked the two part question, so "answered
only part" fired. The verdict was following which question she had been asked,
not what she said.

**Change.** Structured criteria that name the hedge (she does not know, she
repeats someone else) and an option worked out from a detail (a number of
years). Every example inside a criterion uses a field that is NOT in your set,
so the wording cannot win by quoting its own grading cases. P(supports) on
the two misses went 0.61 to 0.02 and 0.78 to 0.15. The good eligibility mint
("on my own, about 5 years") stays supported, at 0.73 where it was 0.85.

**Also changed in the runner:** it now builds the client's `choice_fields`
from your form definition (127 fields, the `InterviewEngine.choiceFields`
walk), since prod has scoped by that catalogue since #1021. 17 of 22 reach Jev
now, not 16. The five that don't are correct to skip: 2 values are outside the
current option set (`spouse_of_us_citizen_3_years` is now `spouse_usc`, and
`citizen` for spouse_citizen_how, which is the outside-options guard's case),
and in 3 the value sits literally in the cited words.

## 2. Two things you should decide, not me

**a. Hair, the one remaining miss.** poc-v02 t19, "canoso", recorded white,
labelled unsupported because gray is an option. Jev says `supports` at about
0.3 under every wording, and a wording that told it to prefer the option that
fits more exactly made it WORSE (and produced a false mark elsewhere, so it
was dropped). My reading is that canoso covers gray AND white, so "fits more
than one option" is the honest verdict. Either reading says the mint should be
marked (as `insufficient` on mine, `contradicts` on yours), so it is a miss
either way, and Jev leans `supports`. What I need from you is whether it is
worth chasing further: I have no wording that moves it without cost somewhere
else.

**b. A lost earlier_turn flag.** conf-v11 t43, `p6.child1.residence =
resides_with_me` on "February eleven two thousand one, she's my daughter...".
Under v1 it flagged 1 or 2 times in 3; under the new wording it flags 0 of 3
(insufficient at 0.44 to 0.49, just under the floor). The agreed policy is to
flag on user_content alone and let the client drop flags on confirmation
turns, so this is a flag the policy wanted and no longer gets. The other four
earlier_turn cases still flag 3 of 3. I shipped anyway because the two real
catches outweigh one near floor policy flag; say so if you weigh it the other
way.

## 3. Seen, NOT chased

conf-v10 t53 (`has_trip2 = yes`, labelled supported): the fact's cited words
are "yes one more, El Salvador again, March twenty twenty four, two weeks",
while her words on that turn are "no, just those two". Cited words that are not
in the turn's utterance are the evidence floor's business
(`drop_facts_without_current_evidence`). **I have not opened that function
tonight** to see whether prod would have dropped this fact before the support
check saw it, so this is an observation, not a finding.

## 4. Unchanged

Marks still never drop a fact. Nothing on the client reads
`facts_unsupported` yet. The deploy note will follow here when #1025 is live
and read back from outside.

## 5. Deploy note: #1025 is LIVE

Merged as `cdbf185`. Read from OUTSIDE: `/health` on cz.shouldersurf.com and
api.ghostpour.com both report `cdbf185`. Read INSIDE the running container:
`app/services/n400_evidence_support.py` has the same sha256 as main
(`e7dbc2c8...`), and each criteria fragment counts the same in both. The
question you ruled on is the one prod asks Jev from this deploy on.

Your rulings are recorded: canoso to white is a KNOWN JEV MISS (Jev leans
supports under every wording), stays labelled unsupported in the set, and
conf-v11 t43's number is reported on every iteration. At this deploy t43 is
0 of 3 marked, P(supports) 0.28 median.

## 6. Review of `contracts/pii-placeholders-2026-09-24.md` (your main 534eff4)

Written answer only, nothing built. Read = I opened it tonight.

**⚠ One blocker first, on the client side, READ not run.** `Privacy/SpokenDigits.swift` maps single digit words only (cero to nueve, zero to nine, um to nove). It has no compounds: "cuarenta y cuatro", "noventa dieciocho", "forty four", "nineteen". That is how Spanish speakers dictate identifiers on this form; it is why GP built `app/services/spanish_numerals.py` (v14 and v15 split "noventa dieciocho" wrong about half the time). On the prompt's own example, "seis dos siete, cuarenta y cuatro, noventa dieciocho", SpokenDigits writes "627" and leaves the rest as words, so PIIRedactor never sees nine digits and masks NOTHING. Then GP (next point) turns those words into digits in the prompt. So for exactly the speakers this app is for, stage one as drafted does not keep the SSN or A-Number away from the model. I have not run your Swift; please run that sentence through SpokenDigits then PIIRedactor and tell me what comes out. The arithmetic you need is in `spanish_numerals.read_groups` (about 80 lines, ours to share).

**Ask 6, "no stage re-derives a value": ONE DOES.** `spanish_numerals.numeral_hint` (called from `chat.py` around line 1782, interviewer lane, Spanish only) reads number words in `user_content` and adds a `{{spoken_numerals}}` line of digits to the prompt. When the phone converts first, it finds nothing and is inert. When the phone misses (above), it is the stage that turns a spoken SSN into clean digits for the model. Once the client covers compounds I would keep it as a LEAK DETECTOR rather than a helper: any interviewer turn where it finds three or more groups gets counted and logged by field, so a masking miss becomes visible instead of silent. About 1 hour with a test, after your fix.

**Ask 3, server typing and shape floors: nothing to change, because there are none.** GP's guards (`n400_interviewer_guard.py`, read) have no SSN, A-Number, phone or email floor; the value checks that exist are the options guards (choice fields only) and the date guard (dates stay unmasked). The typing floors are the client's.

**Ask 4, evidence floor: works unchanged, READ.** `_norm` folds case and whitespace only and keeps brackets, so `[[ssn_1]]` cannot match inside `[[ssn_12]]`. Test to pin it: about 15 minutes.

**Ask 5, streaming: cannot split, READ.** `SentenceSplitter` cuts only after `. ! ? …` followed by whitespace; a placeholder has neither. Test to pin it: about 15 minutes.

**Asks 1 and 2 are PROMPT work, and the bigger cost.** `interviewer-turn` v38 has a whole block that cannot run on a placeholder: A-Number values are "digits only, no letter A"; identifiers are echoed "as separate spoken digits", "ends in" plus the last four; the lane COUNTS digits before minting and speaks both readings digit by digit when two disagree. Each needs an exemption for a masked value or the lane will try to strip, count or read out `[[ANUM_1]]` (my worry is it inventing digits to satisfy the echo rule). That is a v39 in en and es, then a multiturn probe run to check nothing else moves: roughly half a day plus a few dollars of probe.

**⚠ A product question inside ask 2, Scott's word not ours.** Today the applicant HEARS her number read back and can catch a misheard digit by ear. Under masking the lane can only say "your Social Security number", so that check moves to the screen (the client's read-back card) or disappears. The two-candidate disambiguation also moves to the client, since only the client holds the digits. Put that in front of Scott as a trade before building.

**Zero data retention: I CANNOT confirm it.** The lane calls `claude-sonnet-5`. ZDR is an account agreement with Anthropic, not a request field, and nothing in GP's code or memory records one. Scott holds that. Two more recipients to name while we are here: TypeSafe (Jev) receives `applicant_said` for the evidence support check, a second provider whose retention is also unconfirmed; and GP itself stores request content for 30 days (retention purge at startup; that is from GP's notes, NOT re-read tonight), which under this contract would hold placeholders only.

**Order I would suggest:** client compounds fixed and proved on that sentence, then the Scott product call on spoken read-back, then GP's v39 prompt and the three small tests together, then GP's leak counter. Nothing ships before the first step.

## 7. Deploy note: the PII leak counter is LIVE (#1027)

Merged as `cd42b82`. `/health` reads `cd42b82` from outside. Inside the
running container, `n400_pii_leak.py`, `spoken_numbers.py` and `chat.py` are
sha256 identical to main. From this deploy every `n400_interviewer_turn` logs
`n400_pii_unmasked source kind count turn_id` to journald (tag `ghostpour`)
when it sees an unmasked nine or ten digit run or an email; shapes only.
Before your Task 9 wiring ships, every dictated identifier counts: that is the
baseline. Rulings recorded: the NUM floor ships WITH v39; C1 to C13 stay
identical on both sides and you send any diff first.

## 8. Costing stage two (`pii-stage-two-names-dob-2026-09-24.md`), not a go

Read = I opened it tonight: your draft, `interviewer-turn` v38's systemPrompt,
`n400_interviewer_guard.py`, `n400_evidence_support.py`.

**Your restore-into-speech design removes my read-back objection.** If the
phone speaks `[[SSN_1]]` as her digits, she still hears her number. What moves
off the model is only the arithmetic (counting, "ends in", two candidates).

**v39 cost.**
- NUM alone (stage one): placeholders verbatim; never file `[[NUM_n]]`, ask
  which number; echo by writing the placeholder and `:last4`; exempt masked
  values from the digit-only A-Number rule, digit counting and the two
  candidate readout. en and es. Plus the NUM server floor (about 1 hour) and
  the two small tests. About HALF A DAY, plus a probe run.
- All of stage two folded in: the above, plus names (verbatim per word,
  several placeholders in one value, given and family split by position and
  locale, the rules in the next list) and DOB (verbatim, a masked DOB is a
  whole date so never re-ask its day, `:year`). Prompt about ONE DAY. The
  real cost is the probe: `NLTagger` is Apple only, so GP cannot reproduce
  the phone's name masking in Python, and a probe masked by a GP
  approximation would measure GP's masker, not the phone's. **Ask: have Task
  10 emit the MASKED wire request for each of the 167 runs** (plus the
  table); then GP's masked replay is about half a day. Total roughly 1.5 to 2
  days, against about half a day for NUM alone.

**Asks 4 and 5 cost nothing on GP.** No name or date floors exist; the one date
guard (`defer_dates_with_unspoken_day`) acts only on exact `YYYY-MM-DD`
values, so `[[DOB_1]]` passes it untouched (fine, since only whole dates are
masked, but the invented-day protection for DOBs now lives in the phone's
parser). TypeSafe's `applicant_said` IS `user_content` verbatim, so whatever
the phone masks, Jev receives masked.

**Ask 6.** A `dob` kind for the counter: your birth phrase list plus whole date
shapes (ISO, numeric, month name with day and year, spoken), counted by shape.
About 2 hours. ⚠ **Names are NOT countable at GP**: there is no name tagger on
the server, so a name the phone misses is invisible here. Task 10 is the only
measurement names will ever get; please make it count false POSITIVES too
(next list, d).

**What breaks on a masked name that the draft does not name:**
- a. **Garbled names.** v38 tells the lane to read "phonetically plausible
  garbles of names and places" charitably and fix them. On a placeholder it
  cannot, so a misrecognised name goes to the form unless the spoken read-back
  catches it. The read-back becomes the ONLY catch for a misheard name.
- b. **Nicknames and other names.** "People call me Beto": whether that is a
  legal other name or a diminutive of Alberto is judged from the words. On
  `[[NAME_5]]` the lane must ASK instead; needs a rule, or it will guess.
- c. **Partial tags.** Names that are also words ("Paz", "Dolores",
  "Guadalupe", "Mercedes", "Rosario", "Blessing") may be masked in some turns
  and not others, so a value arrives as "[[NAME_1]] Paz". Restore works; the
  leak is partial and, per the above, uncountable here.
- d. **False positives on places.** If the tagger masks a place as a name
  ("born in Santa Rosa", "Lourdes"), the lane loses place reasoning: v38 infers
  a citizen spouse from "she was born in Texas". Task 10 should count places
  tagged as names, not only names missed.
- e. **Summary length.** v38 budgets a Part 2 summary in spoken length ("past
  60: split it after the name"). A placeholder is shorter than the name or date
  it becomes in speech, so the lane will under-count and pack too much into
  one breath. One sentence of prompt, but it has to be there.
- f. Checked, NOT a break: v38 never infers gender from a name (only from known
  facts or her own words), and the "first and last, then ask about a middle
  name" rule counts words, which placeholders preserve.
