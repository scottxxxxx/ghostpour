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
