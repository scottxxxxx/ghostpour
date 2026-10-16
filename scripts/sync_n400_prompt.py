"""Sync one leaf of a served n400 config from the DEPLOYED bundle, then prove it.

Runs INSIDE the prod container:

    scp -i ~/.ssh/gcp_deploy_key scripts/sync_n400_prompt.py \
        scottguida@35.239.227.192:/tmp/
    ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 \
        'docker cp /tmp/sync_n400_prompt.py ghostpour:/tmp/ && \
         docker exec ghostpour python /tmp/sync_n400_prompt.py --expect-sha <merge-commit>'

Reads the admin key from the environment and never prints it.

WHY THIS IS IN THE REPO AND NOT IN /tmp
---------------------------------------
It used to live only in /tmp on two machines, one container recreate away from
the fate of ste_full.json. Worse, the check that matters most was not in it at
all: the operator was expected to confirm the deploy had landed BEFORE running
the sync, and that instruction lived in a chained shell command and in prose.
That is the #948 shape, a command that exists only in chat and is therefore
never run. `--expect-sha` puts the guard inside the thing it guards.

THE TRAP IT EXISTS FOR
----------------------
`sync-from-bundle` copies from the bundle INSIDE THE RUNNING CONTAINER. Run it
before the deploy carrying your change has landed and it pushes the OLD text and
reports success doing it. A sync REPORT and a SERVED VALUE are different events.
So this prints the report, then reads the document back and checks actual
strings.

⚠ `PUT /webhooks/admin/config/{slug}` is a FULL DOCUMENT REPLACE that rejects a
body with no `version`. Never "just PUT systemPrompt": a partial PUT succeeds and
silently deletes the rest of the document. This script never PUTs; it syncs one
leaf pointer, so a dashboard edit to any other key survives.

⚠ PER-VERSION: `PHRASES` and `BLOCK_LINE` below describe ONE prompt version.
They are the v30 (#966) deferral rule and its counterweight. When you ship a new
prompt version, update them to phrases from THAT version, or this reports success
against strings nobody changed. A check that cannot fail is decoration.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SLUG = "n400/interviewer-turn"
ADMIN = "http://localhost:8000/webhooks/admin/config"
HEALTH = "http://localhost:8000/health"

# --- what the served copy must say, PER VERSION -----------------------------
#
# Each version names the ONE phrase that must appear exactly once, the phrases
# that must all be present, and a substring that locates the block the rule
# lives in (a substring, not a line number: v30's check indexed line 83 and a
# reflow would have moved it silently). A sync run against a version whose
# phrases are not listed here refuses, because verifying a new prompt against
# strings nobody changed is a check that cannot fail.
VERSIONS = {
    30: {
        "block_anchor": "DEFERRALS",
        "once": "AND THE CLAUSE MUST NOT BE CONDITIONAL",
        "phrases": [
            "to firm up later IF NEEDED",
            "we can pin the exact day later IF IT MATTERS",
            "to verify the exact days IF NEEDED",
            "Say the return as a fact, not a possibility",
            "if you can find it",
            "when you have your mail in front of you",
            "A condition on WHETHER she has to come back is not",
            "por verificar",
            "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM",
        ],
    },
    31: {
        # The schema block, where the intent line lives. #972: intent is an
        # object, always. The v30 rule must SURVIVE the edit, so its once
        # phrase is asserted present here too.
        "block_anchor": '{\n  "schema_version": 1,',
        "once": '"intent": an OBJECT, never a bare string, {"type": string',
        "phrases": [
            "Write `intent` as an object, always, even when `type` is the only",
            "The bare string is the old shape and is being retired.",
            "`intent.type` is exactly one of:",
            '"target": omitted',
            '"confidence": omitted',
            '"disambiguation": omitted',
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            "if you can find it",                         # and its counterweight
        ],
    },
    32: {
        # The schema block, where the `deferred` line lives. deferred.origin:
        # a capture gap is owed TO her. Edit B REPLACED v31's "goes NOWHERE"
        # passage, so its absence is part of the claim; v30's rule and v31's
        # object intent must survive.
        "block_anchor": '{\n  "schema_version": 1,',
        "once": '"origin": "applicant" or "capture_gap"',
        "phrases": [
            "A PASSING MENTION OF A FACT YOU CANNOT RECORD",
            "said earlier, not yet recorded, ask again",
            "A capture gap NEVER closes a slot",
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        # Absence is checked only beside the presence phrases above: on its own
        # it is true of text that never had the rule.
        "absent": [
            "goes NOWHERE",
        ],
    },
    33: {
        # DEFERRALS names Part 9: v32's capture gap measured 0 of 4 live,
        # with the model's own raw output empty. Edit A replaced the worked
        # example and the "nearest field id" sentence, so the old example's
        # absence is part of the claim. v30, v31 and v32 must survive.
        "block_anchor": "When the applicant cannot give a value",
        "once": "The entry is the record and the reply is not",
        "phrases": [
            "p9.selective_service_registered",
            "the boundary never empties `deferred`",
            "nearest field id you can name",
            "a capture gap is not that decision",
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            "I will ask about your address when we get to that part",
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    34: {
        # The reply-shape rule: every reply opened "Got it, <her answer>"
        # (Scott, 2026-09-15). Edit A replaced the one-line acknowledgement
        # rule, so its absence is part of the claim. v30 to v33 must survive.
        #
        # Shipped as four cuts (v34, v34b, v34c, v34d), each anchored on the
        # last one's replacement text. v34b's vary-the-opener sentences were
        # REPLACED by v34c's "no opener at all", so they are in `absent`: a
        # served copy carrying them is a stale cut, not a new version.
        "block_anchor": "Keep each reply to one to three short spoken sentences",
        "once": "A PLAIN ANSWER GETS NO ECHO",
        "phrases": [
            "ECHO ONLY WHAT COULD HAVE BEEN MISHEARD",
            'Never "Got it, no"',   # sentence-initial since v34b/c reflowed it
            "it begins with the next question itself",    # v34c: no opener
            "it never means no read-back",                # v34d: but still echo
            "a value you do not echo is still minted",
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            "acknowledge what landed, then ask the next thing",
            "Never the same opener two turns running",          # v34b, replaced
            "look at the first word of your own previous line",  # v34b, replaced
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    35: {
        # ASK THE DAY ONCE, with a way out. Scott, build 52: "I was never
        # prompted to provide the exact days." Shipped as five cuts (v35, v35b,
        # v35c, v35d, v35e) after the first probed 0 of 3; each anchors on the
        # previous cut's replacement text. v30 to v34 must survive.
        "block_anchor": "When the applicant cannot give a value",
        "once": "A MONTH AND A YEAR IS ASKED, NOT DEFERRED",
        "phrases": [
            "ONE DAY PER QUESTION",
            "THE MOVED-OUT DAY IS THE VERY NEXT QUESTION",
            "AND THE DAY SHE GAVE IS SAID BACK FIRST",   # v35e
            "a day she gave and never heard back",       # v35e
            "A promise to ASK later is not a promise to verify",
            "as a FLOOR under you and never a move to imitate",
            # Present TWICE (the rule and its worked example), so this is a
            # presence check and must never become a `once`.
            "If you don't know it offhand, we can check it later",
            "A PLAIN ANSWER GETS NO ECHO",               # v34, must survive
            "it never means no read-back",               # v34d, must survive
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            # v35b replaced this; it sat beside the ask-once rule and
            # contradicted it, which is why v35's first cut probed 0 of 3.
            "A partial date is always a deferral",
            "VERY NEXT QUESTION: the turn after",   # v35d's passage, v35e replaced it
            "I'll need the exact day",
            "never demand exact days",
            "acknowledge what landed, then ask the next thing",
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    36: {
        # NAME THE BASIS, NEVER JUDGE IT. From v34d's live receipt: "six years
        # on your own is the general five year path, so that fits" reads back a
        # DERIVED basis and then rules on it, presenting an inference as
        # something the form has checked. The lane did not invent "that fits";
        # our own worked example taught it, in English and in Spanish.
        #
        # MINT THAT BASIS IN THIS SAME RESPONSE is untouched and is in the
        # phrase list on purpose: the blank eligibility box (conf-v20) is the
        # defect that rule exists for, and an edit aimed at the verdict word
        # has no business weakening the mint.
        "block_anchor": "ELIGIBILITY",
        "once": "NAME THE BASIS, NEVER JUDGE IT",
        "phrases": [
            "that's the general five year path",
            "esa es la vía general de cinco años",   # the Spanish half, same rule
            "A reply that names a basis with an empty facts array",
            "MINT THAT BASIS IN THIS SAME RESPONSE",     # must SURVIVE v36
            "AND THE DAY SHE GAVE IS SAID BACK FIRST",   # v35e, must survive
            "A MONTH AND A YEAR IS ASKED, NOT DEFERRED",  # v35, must survive
            "ONE DAY PER QUESTION",                      # v35c, must survive
            "A PLAIN ANSWER GETS NO ECHO",               # v34, must survive
            "it never means no read-back",               # v34d, must survive
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            # The verdict, in both languages. Edit A rewrote the worked example
            # and Edit B the defect sentence, so NO older sentence is left
            # teaching the phrase.
            "that fits",
            "encaja",
            "A partial date is always a deferral",
            "acknowledge what landed, then ask the next thing",
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    38: {
        # THE ORDER IS A REQUIREMENT, NOT A PREFERENCE. GP streams the reply
        # sentence by sentence for TTS (PR #1003) and its release check runs
        # the real checkpoint refusal on the PARTIAL object, which it can only
        # do once facts, deferred and section_checkpoint are parsed; without
        # them it buffers the whole turn, on exactly the read-back turns
        # streaming is for. The served v36 already ordered them; this makes
        # the order something the model is TOLD rather than something it
        # happens to do (16 of 16 real responses complied before the cut).
        # One sentence, no moved blocks, cut from v36 by the auditor. v37 and
        # v37b stay shelved; v38 skips the number so two texts never share one.
        #
        # The ORIGINAL reason for the order must survive word for word: the
        # edit extends that sentence rather than replacing its argument.
        "block_anchor": "the read-back is the `reply` string of an object, never prose on its own",
        "once": "THE ORDER IS A REQUIREMENT, NOT A PREFERENCE",
        "phrases": [
            "`facts`, `deferred` AND `section_checkpoint` ARE ALWAYS WRITTEN BEFORE `reply`",
            "starts speaking each sentence of `reply` the moment that sentence is complete",
            "A reply written before them is not wrong, it simply cannot be spoken until the whole turn has arrived",
            "the reply LAST, because each later field must follow the earlier ones",  # the original reason, must survive
            "`asking` written after `facts` can never name a node you just minted",
            "No prose, no markdown, no code fences.",
            "NAME THE BASIS, NEVER JUDGE IT",             # v36, must survive
            "MINT THAT BASIS IN THIS SAME RESPONSE",     # v36 kept it, must survive
            "AND THE DAY SHE GAVE IS SAID BACK FIRST",   # v35e, must survive
            "A MONTH AND A YEAR IS ASKED, NOT DEFERRED",  # v35, must survive
            "ONE DAY PER QUESTION",                      # v35c, must survive
            "A PLAIN ANSWER GETS NO ECHO",               # v34, must survive
            "it never means no read-back",               # v34d, must survive
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            # v36's verdict must stay gone; a v38 sync that brought it back
            # would be a stale cut wearing a new number.
            "so that fits",
            "así que encaja",
        ],
    },
    39: {
        # ONE OPTIONAL REVIEW (Scott via the auditor, 2026-09-26, after build
        # 101): no "is that complete and correct?" after every part. A part
        # boundary summarizes in one sentence and opens the next part in the
        # same reply (section_checkpoint awaiting_confirmation FALSE, asking
        # set); when the agenda is empty the lane offers the review ONCE. The
        # opener is softened to match. Every v38 phrase must survive. The
        # block is the SECTION CHECKPOINTS section, where the new end lives.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "THE END OF THE INTERVIEW: ONE OPTIONAL REVIEW",
        "phrases": [
            "A part boundary NEVER asks whether the part is complete and correct",
            "Carry `section_checkpoint` with `awaiting_confirmation` false",
            '"awaiting_confirmation": boolean, FALSE at a part boundary',
            "they will see the whole form before anything is final",
            "the LOWEST PART NUMBER YOU HAVE NOT READ BACK YET in this review",
            # Case 3b5d206f (2026-09-25): three countries named at the trips
            # gate were deferred with their missing dates, and a deferred field
            # leaves the agenda and VOLUNTEER FIELDS for good, so none was filed.
            "DEFER ONLY WHAT SHE DID NOT SAY",
            # The first v39 cut skipped the offer on an empty agenda and closed
            # (probe, 2026-09-26): the review is optional for her, not for the lane.
            "THE REVIEW IS HER OPTION AND YOUR OBLIGATION",
            "your reply ENDS ON THAT OFFER",
            "to trip1, trip2 and trip3 in the order she named them",
        ],
        "absent": [
            "so that fits",
            "así que encaja",
            # The per-part confirmation Scott retired must not come back in a
            # stale cut wearing the new number.
            "One thing per reply at a checkpoint",
            "the FINAL READ-BACK begins",
            "is that all complete and correct?",
            "nothing is filed until they review",
        ],
    },
    40: {
        # Spectrum round 1 (the auditor, 2026-09-27, contract
        # spectrum-round1-lane-findings-2026-09-27.md): five personas end to
        # end on v39. Sweep the open deferrals once before the review offer;
        # press once for a vague date; names (two surnames for everyone,
        # family-first order, never an unsaid surname); a named earlier job is
        # a yes, a current job ends present, an idle stretch has no employer;
        # a relative, work or asylum green card is general_provision; the
        # checklist adds volunteered dates, read-back only what is filed, and
        # corrections re-file. Every v39 phrase must survive.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "BEFORE THE OFFER, SWEEP WHAT WAS LEFT, ONCE",
        "phrases": [
            "the reply that finds the agenda empty and the sweep done MAKES the offer",
            "TWO SURNAMES ARE ONE FAMILY NAME, FOR EVERY PERSON ON THE FORM",
            "A FAMILY-FIRST NAME (Vietnamese, Chinese, Korean or Hungarian order)",
            "NEVER FILE A SURNAME SHE DID NOT SAY",
            "IS THE GENERAL FIVE-YEAR PATH: mint `general_provision`",
            "A VAGUE DATE IS DIFFERENT FROM A PARTIAL ONE",
            "A JOB SHE NAMES IS A JOB",
            'A JOB SHE STILL HAS ENDS "present"',
            "A RETIRED OR UNEMPLOYED STRETCH HAS NO EMPLOYER AND NO WORKPLACE",
            "is minted, or deferred with its partial (2020-05), in that same response, never dropped",
            "a correction re-files",
        ],
        "absent": [
            # v39's name rule, which split "Ernesto Delgado Ruiz" into a middle
            # and a last name, and v39's offer trigger, which skipped the sweep.
            "If the answer has three words, that is first, middle and last.",
            "the reply that finds the agenda empty MAKES the offer",
        ],
    },
    41: {
        # Spectrum round 3 (the auditor, 2026-09-28, contract
        # spectrum-round3-lane-findings-2026-09-28.md) on v40. The sweep reads
        # every deferred line from every part; every named job is a row; a city
        # is never a ZIP; the Part 9 recap says what she answered; refinements
        # re-file; a missing surname is asked next, once; five years covered
        # closes the list; Scott's two rulings (the idle place filed only if
        # volunteered; Part 9 one group per turn). The idle heading is REWORDED
        # by that ruling, so it leaves the list and its old form is pinned absent.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "THE SWEEP READS EVERY LINE OF KNOWN FACTS MARKED",
        "phrases": [
            "EVERY JOB SHE NAMES IS A ROW",
            "A RETIRED OR UNEMPLOYED STRETCH HAS NO EMPLOYER:",
            "are filed only when she volunteers them (Scott, 2026-09-28)",
            "the surname is asked in the NEXT question, once, by name",
            "FIVE YEARS COVERED CLOSES THE LIST",
            "A CITY IS NEVER A ZIP",
            "A REFINEMENT IS A CORRECTION TOO",
            "the repeat is its SHORT form",
            "while Part 9 stays one group per turn",
            "the answer wins: take it, intent answer",
            "no to the record questions and yes to the oath",
        ],
        "absent": [
            "A RETIRED OR UNEMPLOYED STRETCH HAS NO EMPLOYER AND NO WORKPLACE",
        ],
    },
    42: {
        # Round 4 on v41 (the auditor, 2026-09-28): five years covered closes
        # the ADDRESS list (live repro priya-r5 t6), grouping pairs, a
        # correction's "No." answers nothing else, a named offense is filed at
        # first mention, the closing gate is the one after the last row, a
        # probation she never had is the empty string (never "no"), a count
        # with the list closes it, Part 5 opens with marital status alone, and
        # smaller wording items. The SSN read-back guard ships in code.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "belongs to the correction and answers nothing else",
        "phrases": [
            "The same for addresses: the address question asks for every address in the last five years",
            "The same for these pairs, each one natural sentence",
            "re-file the whole name.",
            "is self-employed on that row, and no workplace ZIP is asked for it",
            "that is a WAIT: say you will wait",
            "in Vietnamese order the family name comes FIRST",
            "a question never opens with a reason built from another answer",
            "an offense she names is FILED the turn she names it",
            "the gate that closes a list is the one after the LAST row on file",
            "leaves p9.probation_completed as the empty string, never \"no\"",
            "mint the next gate \"no\" in THAT response",
            "open Part 5 with the marital status alone",
            "a sweep names exactly what is open",
        ],
        "absent": [],
    },
    43: {
        # Round 5 on v42 (the auditor, 2026-09-28), probed from LIVE v42
        # requests. Grouping scoped to the next short-fact lines; a gate
        # question names its row; an address in passing is a row; app work's
        # ZIP is confirmed empty; volunteer fields are never questions; the
        # sweep says the day; five years covered opens the next part; stated
        # names filed; no proposed surnames; no "Since" openers. Code: the
        # stated trip count closes the list, question_still_spoken.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "VOLUNTEER FIELDS IS FOR FILING WHAT SHE VOLUNTEERS, NEVER A LIST OF QUESTIONS",
        "phrases": [
            "GROUP THE NEXT TWO OR THREE SHORT-FACT LINES ON THE AGENDA",
            "A GATE QUESTION NAMES THE ROW IT FOLLOWS",
            "has NO workplace address to complete",
            "is missing its DAY, so say",
            "ONCE YOU HAVE SAID IT COVERS FIVE YEARS, OF JOBS OR OF ADDRESSES",
            "a name she STATES is FILED, never re-deferred",
            "never propose a surname she did not say",
            "trip dates go to the row whose COUNTRY they belong to",
            "a question never begins \"Since\" followed by her earlier answer",
        ],
        "absent": [],
    },
    44: {
        # Round 6 on v43 (the auditor, 2026-09-28). Most of v44 is CODE (the
        # floor restore, the digit count, probation never had); these are the
        # prompt lines: grouping adapts to the speaker, the sweep lists Parts
        # 2, 4 and 7 before Part 9, every filed identifier is read back,
        # phones in groups, children's names alike, recaps name every child.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "a long Part 9 never crowds them out",
        "phrases": [
            "once she has answered only the first half of a grouped question twice, she is a one-fact speaker",
            "EVERY identifier you file is read back in that same reply",
            "a phone is read in its groups",
            "after \"no middle name\" in her answer the middle name is never asked",
            "a recap names every child she supports, as filed",
        ],
        "absent": [],
    },
    45: {
        # Round 7 on v44 (the auditor, 2026-09-28): the client's pace clause
        # in APPLICANT CONTEXT decides grouping mechanically, plus lane items.
        # The rest of v45 is code (the settled question replaced, the floor's
        # date equivalence and same-city state).
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "a job that ended before the five-year window never replaces her CURRENT idle row",
        "phrases": [
            "APPLICANT CONTEXT'S PACE CLAUSE DECIDES THIS, MECHANICALLY",
            "\"pace: one fact per turn\" means ONE question per turn",
            "\"pace: full paragraphs\" means you group the next two or three short-fact lines EVERY time",
            "a nickname is not an other name",
            "someone never married has no times-married answer, never 0",
            "a part is never closed while one of its agenda lines is still open",
        ],
        "absent": [],
    },
    46: {
        # Scott's build-108 run (via the auditor, 2026-09-28): the A-Number
        # is asked plainly; where it is on the card is a gloss only when she
        # asks or hesitates.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        # The A-Number line sits BEFORE the block, so the in-block check keeps
        # v45's anchor phrase and the new rule is carried as a phrase.
        "once": "a job that ended before the five-year window never replaces her CURRENT idle row",
        "phrases": [
            "Ask for the A-Number plainly (\"Now, your A-Number?\")",
            "WHERE IT IS ON THE CARD IS A GLOSS, SAID ONLY WHEN SHE ASKS OR HESITATES",
        ],
        "absent": ["Ask for the A-Number as the number usually shown on the Green Card"],
    },
    47: {
        # Scott, 2026-09-29, via the N400 client (contracts/p9-one-by-one-
        # 2026-09-29.md): Part 9 is one form item per turn; the first line's
        # lead-in is read once; items the phone filed are never asked again.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "a job that ended before the five-year window never replaces her CURRENT idle row",
        "phrases": [
            "PART 9 IS ONE QUESTION AT A TIME (Scott, 2026-09-29)",
            "a no files THAT item only, never the items after it",
            "Never say \"group\" about Part 9",
            "THE FIRST PART 9 LINE'S LEAD-IN IS PART OF ITS QUESTION",
            "never ask it again and never read the lead-in again",
            "Part 9 is ONE ITEM PER TURN in every pace",
            "while Part 9 is one item per turn",
            "In Part 9 a yes or a no answers the one question asked",
        ],
        "absent": [
            "Part 9 stays one group per turn",
            "A battery on the agenda (arrests, affiliations, armed groups) is fine to ask as one question",
            "Yes to all?",
            "a Part 9 battery 75",
            "group by group",
        ],
    },
    48: {
        # The client's orientation-line contract (2026-09-29): with the
        # oriented clause the opening is the question alone, in every locale.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "a job that ended before the five-year window never replaces her CURRENT idle row",
        "phrases": [
            "WHEN APPLICANT CONTEXT CARRIES THE ORIENTED CLAUSE",
            "the opening is THE QUESTION ALONE",
            "\"is this a person?\" asked later still gets \"an assistant, not a person\"",
            "the Spanish opening says NONE of the preamble either",
        ],
        "absent": [],
    },
    49: {
        # The client's form-wording contract (2026-09-30): questions the
        # N-400 or its Instructions word are read as given.
        "block_anchor": "SECTION CHECKPOINTS\nA part is done when SECTION BOUNDARY is present",
        "once": "a job that ended before the five-year window never replaces her CURRENT idle row",
        "phrases": [
            "FORM WORDING IS READ AS GIVEN (Scott, 2026-09-30)",
            "Is your household income less than or equal to 400% of the Federal Poverty Guidelines?",
            "Its yes and no are about HER INCOME, not her wishes",
            "EXCEPT the interpreter question, which is read AS GIVEN",
            "with no view on who counts as an interpreter",
            "say you can't give legal advice",
            "were asked before we begin, or at their gates when not answered there",
            "that is a CORRECTION of p1.eligibility_basis to that box's option id",
        ],
        "absent": [
            "USCIS lowers the filing fee for lower household incomes",
            "never with the income threshold recited at them",
            "An app speaking her language is not an interpreter",
            "and were never asked aloud, so the review is the only place she hears them",
        ],
    },
    50: {
        # Scott's comparison run 3 (2026-10-07): a prior address filed from
        # 2019-03-03 to 2019-03-02. The guard refuses it in code
        # (n400_interviewer_guard.date_inversions); this line asks first.
        "block_anchor": "HOW TO TALK\nTalk like a person doing intake",
        "once": "A ROW NEVER ENDS BEFORE IT STARTS",
        "phrases": [
            "would make an address or a job end before it starts, against the other date in KNOWN FACTS or in this same answer, do not file it",
            "Tell her plainly which two dates conflict",
            "and ask which one is right",
            # Run 3, 02:59:38Z: "I'm married to Wenceslao YARBOROUGH D-A-V-I-D"
            # filed spouse_middle_name = David. Masked names and unmasked
            # spellings disagree, so the mismatch case is the one that matters.
            "LETTERS SPELLED AFTER A NAME ARE ITS SPELLING",
            "those letters spell the name she just said, never another name and never a middle name",
            "When they spell something else, file neither; say both and ask which one is right.",
        ],
        "absent": [],
    },
    51: {
        # Scott's live run on build 128 (case 88761ff1, 2026-10-07 ~15:32Z):
        # a pending green card day follow-up AND the next part's question in
        # one reply, so the card and the voice disagreed. And ~15:38Z: Part 4
        # asked for the city onboarding already had.
        "block_anchor": "HOW TO TALK\nTalk like a person doing intake",
        "once": "A FOLLOW-UP KEEPS THE PART OPEN",
        "phrases": [
            "that follow-up is the ONLY question in this reply, with `asking` naming the node it belongs to",
            "no part summary and no next part's question, even when SECTION BOUNDARY is present",
            "(a pending follow-up and the next part's first question are two)",
            "A PIECE ALREADY ON FILE IS NEVER ASKED",
            "the address question asks only for the pieces still missing and may read back what is on file",
        ],
        "absent": [],
    },
    52: {
        # Scott's run on build 130 (case 09a8d9e9, 2026-10-07 16:05 to 16:33Z),
        # four defects from the phone's turnlog via the auditor.
        "block_anchor": "HOW TO TALK\nTalk like a person doing intake",
        "once": "A YEAR IS ONLY A YEAR",
        "phrases": [
            "Never say or file a month or a day she did not speak",
            "defer the field with the year alone as `partial_value`",
            "A FILED VALUE STAYS FILED",
            "is never cleared, emptied, deferred again or asked again unless WHAT THE APPLICANT JUST SAID corrects it",
            "A response carries a fact or a deferral only for a field her current words touch",
            "A UNIT AND A ZIP ARE TWO NUMBERS",
            "never join numbers she said apart",
            "A START ON FILE COUNTS WHETHER IT IS FILED OR A DEFERRED PARTIAL",
            "A gate KNOWN FACTS already holds is answered and is never asked, whatever its value.",
        ],
        "absent": [],
    },
    53: {
        # Scott's device runs, builds 146/147 (2026-10-08), the auditor's
        # redundant-asks audit: cases 2cb53ac8, 472832fe, 32a5de34.
        "block_anchor": "HOW TO TALK\nTalk like a person doing intake",
        "once": "THE GREEN CARD DATE IS ON HER CARD",
        "phrases": [
            "p2.lpr_date is printed on the green card as Resident Since",
            "Defer only after she says she doesn't know or will check.",
            "that same reply asks for the day once, as its only question, and writes no deferral for it",
            "and only then is it deferred with its partial (2020-05)",
            "that text is not what you ask. Ask only for the ids listed, by name",
            "Never ask for a full name while KNOWN FACTS holds the first and last name.",
            "Never ask which part of a two-word name is the family name unless she gave it in family-first order or said so herself.",
            "the other part is her first name: file it and do not ask",
        ],
        # The v52 clause that let a volunteered month and year be deferred
        # without the day ever being asked.
        "absent": ["is minted, or deferred with its partial (2020-05), in that same response"],
    },
    54: {
        # Scott's build 149 run (case 28f9bb5e t_008, 2026-10-09): parents
        # answered at t_007, and the lane still asked the canned pair
        # sentence the prompt itself prescribed.
        "block_anchor": "HOW TO TALK\nTalk like a person doing intake",
        "once": "A PAIR NEVER CARRIES AN ANSWERED HALF",
        "phrases": [
            "the next question groups the next short lines STILL ON THE AGENDA",
            "never gender alone while its partner is still open",
            "when one half of any pair below is in KNOWN FACTS or missing from the agenda, ask only the other half",
        ],
        # The unconditional canned sentence the lane copied.
        "absent": ['the next question is "Are you male or female, and was either of your parents a U.S. citizen before you turned 18?", never gender alone;'],
    },
    55: {
        # A tester's es run on build 156 (2026-10-09), three lane findings from
        # the auditor: a date read back in full with no fact, the day and the
        # month of p4.prior_address1.to given on different turns and never
        # joined, and a "Sí" to the deferral sweep answered with "las dejamos
        # pendientes por ahora". The guard half (a day she gave before its
        # month) is in n400_interviewer_guard.defer_dates_with_unspoken_day.
        "block_anchor": "DEFERRALS\nWhen the applicant cannot give a value now",
        "once": "A DATE YOU READ BACK IN FULL IS A FACT IN THAT SAME RESPONSE",
        "phrases": [
            "THE DAY AND THE MONTH MAY ARRIVE ON DIFFERENT TURNS, AND THE WHOLE DATE IS MINTED THE TURN THE LAST PIECE LANDS",
            "mint the whole date citing the month's words",
            "GP keeps a date that is hers from one of her last three lines, but you mint it the turn the last piece lands, never a turn later.",
            "a start date said whole and read back",
            "even when its day and its month came on different turns",
            'A YES TO THE SWEEP IS NOT "LATER"',
            "the reply asks for the FIRST item the sweep named, by name, as its only question",
            "Only her no, or her saying she does not have them, leaves them deferred.",
            "it is never clarified with an either/or a bare \"sí\" cannot answer",
            "ask for the first item directly.",
        ],
        "absent": [],
    },
    56: {
        # Scott's run on build 160, live v55 (2026-10-12): "No, I told you
        # January 1st, 1999" pushed back on a deferral, and the lane filed the
        # "No" as p2.has_other_names, a question he never answered.
        "block_anchor": "BEFORE YOU WRITE, IN ANY LOCALE, CHECK",
        "once": 'A "NO" THAT PUSHES BACK ON ANYTHING YOUR LAST LINE SAID IS THE SAME',
        "phrases": [
            'when her "No" is followed by "I told you", "I said", "I already" or a value restated',
            'the "No" answers nothing, the trailing question is still open, and this reply files the restated value and asks that question again',
            'filing has_other_names "no" from that "No" is the defect, live on v55',
        ],
        "absent": [],
    },
    57: {
        # The auditor's privacy scoping (2026-10-13): the phone masks the date
        # of birth (real year, fake month and day), so "Work it out" had the
        # model judge 18 or over from a date that is not exact. The phone
        # sends p1.age_over_18, computed from the real date; v57 points there.
        "block_anchor": "a fact in KNOWN FACTS is authoritative whoever derived it",
        "once": "never work out an age from a date of birth: the date of birth you see is not exact",
        "phrases": [
            "Never ask whether they are 18 or over, and never work out an age",
            "Use the p1.age_over_18 fact the app sends.",
        ],
        "absent": [
            "never ask whether they are 18 or over. Work it out.",
        ],
    },
    58: {
        # Scott's live run on build 174 (case 49ea066d, t_016, 2026-10-13):
        # two residences, never "that's all", and the lane said "that covers
        # five years back to November 2021" (the window started in October)
        # and minted p4.has_prior_address2 "no". The app owns the window math
        # (DerivedFacts derives the gate from the row's start); v58 takes it
        # away from the lane.
        "block_anchor": "Whether the addresses or jobs on file cover the years the form asks",
        "once": "A HISTORY GATE IS ANSWERED ONLY BY HER",
        "phrases": [
            "is the app's call, never yours: never say or imply that a history covers the required years",
            'is "no" only when she says in her own words that there is nothing more',
            "The same for addresses: when the app's own window math settles a has-prior-address gate",
            "NEVER TELL HER HOW FAR BACK HER JOBS OR ADDRESSES REACH",
        ],
        "absent": [
            "FIVE YEARS COVERED CLOSES THE LIST",
            "The same for addresses: the address question asks for every address in the last five years",
            "ONCE YOU HAVE SAID IT COVERS FIVE YEARS, OF JOBS OR OF ADDRESSES",
            "A START ON FILE COUNTS WHETHER IT IS FILED OR A DEFERRED PARTIAL",
            "say the window is covered and move on",
        ],
    },
    59: {
        # The auditor's boundary-date runs, 2026-10-14, on served v58. Three
        # defects: the address row re-asked right after its read-back (the
        # client logged asking_refused:not_in_agenda on q_p4_current_address),
        # "Since she's single..." said TO the applicant (the instructions call
        # her "she" ~300 times and never said that word is not for speaking),
        # and the job gate asked as a bare "before that" (the prompt's own
        # p7.has_job3 example taught it).
        "block_anchor": "You are conducting a spoken intake interview",
        "once": "YOU SPEAK TO THE APPLICANT, NEVER ABOUT THEM",
        "phrases": [
            'addresses the applicant in the second person only: "you" in English, "usted" in Spanish, "você" in Portuguese',
            'never "she", "he" or "they" about them',
            "A ROW YOU HAVE READ BACK OR FILED IS NEVER ASKED AGAIN",
            "its node has left the AGENDA, so the next question is the next AGENDA line",
            'for work that is the earliest employer by the name KNOWN FACTS holds ("What did you do before Alamo Builders?")',
            "BY ITS EMPLOYER'S NAME as KNOWN FACTS holds it, never before one in the middle",
        ],
        "absent": [
            'asked "anything before that?" at `p7.has_job3`',
        ],
    },
    60: {
        # Case bf7aed58, t_017, 2026-10-14. The response filed
        # p4.has_prior_address1 = yes (she named a prior address) and its reply
        # asked "did you live anywhere else before Tamarack Road?", the NEXT
        # gate, but `asking` named q_p4_prior_gate1, the gate its own facts had
        # just answered. GP drops an asking whose node this response filled.
        # The rule widens the asking paragraph in MOVING THE INTERVIEW in place.
        "block_anchor": "MOVING THE INTERVIEW\n",
        "once": "`ASKING` NAMES THE NODE OF YOUR REPLY'S LAST QUESTION, NEVER ONE YOUR OWN FACTS JUST ANSWERED",
        "phrases": [
            "decide the last question your reply will ask and name the agenda node whose question that is",
            '"Did you live anywhere else before Tamarack Road?", that is the NEXT gate, so `asking` is '
            '{"node_id": "q_p4_prior_gate2", "field_ids": ["p4.has_prior_address2"]}, never q_p4_prior_gate1',
            "The same holds for the next job, trip, child or marriage: the gate or row your reply asks about is the node `asking` names.",
        ],
        "absent": [],
    },
    61: {
        # App build 179 (bcb1843, 2026-10-14) withholds "if any" rows (child 2
        # to 4, the USCIS account number, the spouse A-Number) until their
        # gate or count is answered, so on q_p6_children_count the agenda has
        # no open Part 6 line and the client sent SECTION BOUNDARY. v60 read
        # "A part is done when SECTION BOUNDARY is present" literally, so "two
        # kids" would close Part 6 before any child was asked. No guard can see
        # it: the checkpoint guard reads only agenda lines.
        "block_anchor": "SECTION CHECKPOINTS",
        "once": "A GATE OR COUNT THAT OPENS ROWS DOES NOT END ITS PART",
        "phrases": [
            "the rows it opens are listed by the app on the next turn, even when SECTION BOUNDARY is present",
            '("What is your first child\'s full name and date of birth?"), and has no summary and no section_checkpoint',
            'Only "no", "none" or zero closes the part there.',
        ],
        "absent": [],
    },
    62: {
        # Scott's draft via the auditor (2026-10-14): build 180 sends
        # metadata.today on every turn, so "May 1 last year" can be resolved,
        # and the lane may OFFER a likely value as a yes or no question from
        # six named anchors, minting it only on her yes. The template gains
        # TODAY, which this script does not sync: /userPromptTemplate and
        # /optionalVariables go by a scoped sync, read back by string.
        "block_anchor": "THE RULE THAT OVERRIDES CHARITY",
        "once": "AN ANCHOR MAY BE OFFERED, NEVER FILED: when TODAY, KNOWN FACTS or APPLICANT CONTEXT makes one value likely",
        "phrases": [
            "TODAY IS HER OWN CALENDAR DAY, from the TODAY line of the request.",
            "is still a VAGUE DATE and gets that rule's one follow-up",
            "When TODAY is empty, never resolve a relative year: ask which year.",
            "you mint it only on her yes, with her yes as its provenance.utterance",
            "the YEAR of her date of birth, never its month or day",
            "Offer one value at a time, and never a day she did not say unless KNOWN FACTS holds it.",
            "an offer she has not confirmed is not a fact",
            "Asking one as a yes or no question is not filling it (AN ANCHOR MAY BE OFFERED, NEVER FILED).",
            "AT THE TRIP QUESTION, A TRIP HOME GETS THE COUNTRY OFFERED IN THE SAME REPLY",
            "A CAPTURE GAP WITH HER WORDS IS OFFERED BACK, NEVER ASKED COLD",
            "every open field of every part before Part 9, and Part 9's own only while you are standing in Part 9.",
            "A LATER PART'S VALUE SAID EARLY IS MINTED, NEVER HELD AS A CAPTURE GAP",
            "because no field id for it is in the agenda or in VOLUNTEER FIELDS, is a CAPTURE GAP",
            "A GAP QUESTION THE APP WROTE IS ASKED AS WRITTEN",
            "THE STRETCH IN THE APP'S QUESTION IS AN ANCHOR OFFERED, NEVER FILED ON ITS OWN",
            "NEVER SAY OR IMPLY SHE IS CHECKING, LOOKING UP OR VERIFYING ANYTHING UNLESS HER OWN WORDS SAID SO",
            "A CAPTURE GAP ON A DATE WHOSE PARTIAL IS A YEAR OR A MONTH AND YEAR IS A DAY STILL OWED, NOT AN OFFER",
            "which is how the app learns the day was asked and turns the line into an ordinary deferral.",
            "`field_ids` LISTS ONLY THE FIELDS YOUR SPOKEN QUESTION ASKS, NEVER EVERY FIELD ON THE NODE",
        ],
        "absent": [
            "for the current part and the next.",
            "because no field id for it is in front of you, is a CAPTURE GAP",
            "except the exact move-in date, which you are checking",
        ],
    },
}

# v39 carries every v38 phrase: a v39 sync that lost one is a regression of v38.
VERSIONS[39]["phrases"] = VERSIONS[39]["phrases"] + VERSIONS[38]["phrases"]
# v40 carries every v39 phrase and every v39 retirement, extended rather than
# copied, so a v39 rule cannot fall out of v40's read-back by an edit to one list.
VERSIONS[40]["phrases"] = VERSIONS[39]["phrases"] + VERSIONS[40]["phrases"]
VERSIONS[40]["absent"] = VERSIONS[39]["absent"] + VERSIONS[40]["absent"]
# v41 carries every v40 phrase except the idle heading Scott's ruling reworded.
VERSIONS[41]["phrases"] = [p for p in VERSIONS[40]["phrases"]
                           if p != "A RETIRED OR UNEMPLOYED STRETCH HAS NO EMPLOYER AND NO WORKPLACE"] + VERSIONS[41]["phrases"]
VERSIONS[41]["absent"] = VERSIONS[40]["absent"] + VERSIONS[41]["absent"]
# v42 carries every v41 phrase.
VERSIONS[42]["phrases"] = VERSIONS[41]["phrases"] + VERSIONS[42]["phrases"]
VERSIONS[42]["absent"] = VERSIONS[41]["absent"] + VERSIONS[42]["absent"]
# v43 carries every v42 phrase.
VERSIONS[43]["phrases"] = VERSIONS[42]["phrases"] + VERSIONS[43]["phrases"]
VERSIONS[43]["absent"] = VERSIONS[42]["absent"] + VERSIONS[43]["absent"]
# v44 carries every v43 phrase.
VERSIONS[44]["phrases"] = VERSIONS[43]["phrases"] + VERSIONS[44]["phrases"]
VERSIONS[44]["absent"] = VERSIONS[43]["absent"] + VERSIONS[44]["absent"]
# v45 carries every v44 phrase.
VERSIONS[45]["phrases"] = VERSIONS[44]["phrases"] + VERSIONS[45]["phrases"]
VERSIONS[45]["absent"] = VERSIONS[44]["absent"] + VERSIONS[45]["absent"]
# v46 carries every v45 phrase.
VERSIONS[46]["phrases"] = VERSIONS[45]["phrases"] + VERSIONS[46]["phrases"]
VERSIONS[46]["absent"] = VERSIONS[45]["absent"] + VERSIONS[46]["absent"]
# v47 carries every v46 phrase except the Part 9 group line Scott's
# 2026-09-29 ruling retired.
VERSIONS[47]["phrases"] = [p for p in VERSIONS[46]["phrases"]
                           if p != "while Part 9 stays one group per turn"] + VERSIONS[47]["phrases"]
VERSIONS[47]["absent"] = VERSIONS[46]["absent"] + VERSIONS[47]["absent"]
# v48 carries every v47 phrase.
VERSIONS[48]["phrases"] = VERSIONS[47]["phrases"] + VERSIONS[48]["phrases"]
VERSIONS[48]["absent"] = VERSIONS[47]["absent"] + VERSIONS[48]["absent"]
# v49 carries every v48 phrase.
VERSIONS[49]["phrases"] = VERSIONS[48]["phrases"] + VERSIONS[49]["phrases"]
VERSIONS[49]["absent"] = VERSIONS[48]["absent"] + VERSIONS[49]["absent"]
# v50 carries every v49 phrase, and v49's once line as a phrase (v50 checks a new once).
VERSIONS[50]["phrases"] = VERSIONS[49]["phrases"] + [VERSIONS[49]["once"]] + VERSIONS[50]["phrases"]
VERSIONS[50]["absent"] = VERSIONS[49]["absent"] + VERSIONS[50]["absent"]
# v51 carries every v50 phrase, and v50's once line as a phrase (v51 checks a new once).
VERSIONS[51]["phrases"] = VERSIONS[50]["phrases"] + [VERSIONS[50]["once"]] + VERSIONS[51]["phrases"]
VERSIONS[51]["absent"] = VERSIONS[50]["absent"] + VERSIONS[51]["absent"]
# v52 carries every v51 phrase, and v51's once line as a phrase (v52 checks a new once).
VERSIONS[52]["phrases"] = VERSIONS[51]["phrases"] + [VERSIONS[51]["once"]] + VERSIONS[52]["phrases"]
VERSIONS[52]["absent"] = VERSIONS[51]["absent"] + VERSIONS[52]["absent"]
# v53 carries every v52 phrase but the volunteered-date clause it rewrote (the
# auditor, 2026-10-08), and v52's once line as a phrase.
VERSIONS[53]["phrases"] = [p for p in VERSIONS[52]["phrases"]
                           if p != "is minted, or deferred with its partial (2020-05), in that same response, never dropped"] \
    + [VERSIONS[52]["once"]] + VERSIONS[53]["phrases"]
VERSIONS[53]["absent"] = VERSIONS[52]["absent"] + VERSIONS[53]["absent"]
# v54 carries every v53 phrase, and v53's once line as a phrase.
VERSIONS[54]["phrases"] = VERSIONS[53]["phrases"] + [VERSIONS[53]["once"]] + VERSIONS[54]["phrases"]
VERSIONS[54]["absent"] = VERSIONS[53]["absent"] + VERSIONS[54]["absent"]
# v55 carries every v54 phrase, and v54's once line as a phrase.
VERSIONS[55]["phrases"] = VERSIONS[54]["phrases"] + [VERSIONS[54]["once"]] + VERSIONS[55]["phrases"]
VERSIONS[55]["absent"] = VERSIONS[54]["absent"] + VERSIONS[55]["absent"]
# v56 carries every v55 phrase, and v55's once line as a phrase.
VERSIONS[56]["phrases"] = VERSIONS[55]["phrases"] + [VERSIONS[55]["once"]] + VERSIONS[56]["phrases"]
VERSIONS[56]["absent"] = VERSIONS[55]["absent"] + VERSIONS[56]["absent"]
# v57 carries every v56 phrase, and v56's once line as a phrase.
VERSIONS[57]["phrases"] = VERSIONS[56]["phrases"] + [VERSIONS[56]["once"]] + VERSIONS[57]["phrases"]
VERSIONS[57]["absent"] = VERSIONS[56]["absent"] + VERSIONS[57]["absent"]
# v58 carries every v57 phrase except the four coverage rules it retires
# (they move to its absent list), and v57's once line as a phrase.
VERSIONS[58]["phrases"] = [p for p in VERSIONS[57]["phrases"] if p not in VERSIONS[58]["absent"]] \
    + [VERSIONS[57]["once"]] + VERSIONS[58]["phrases"]
VERSIONS[58]["absent"] = VERSIONS[57]["absent"] + VERSIONS[58]["absent"]
# v59 carries every v58 phrase, and v58's once line as a phrase.
VERSIONS[59]["phrases"] = VERSIONS[58]["phrases"] + [VERSIONS[58]["once"]] + VERSIONS[59]["phrases"]
VERSIONS[59]["absent"] = VERSIONS[58]["absent"] + VERSIONS[59]["absent"]
# v60 carries every v59 phrase, and v59's once line as a phrase.
VERSIONS[60]["phrases"] = VERSIONS[59]["phrases"] + [VERSIONS[59]["once"]] + VERSIONS[60]["phrases"]
VERSIONS[60]["absent"] = VERSIONS[59]["absent"] + VERSIONS[60]["absent"]
VERSIONS[61]["phrases"] = VERSIONS[60]["phrases"] + [VERSIONS[60]["once"]] + VERSIONS[61]["phrases"]
VERSIONS[61]["absent"] = VERSIONS[60]["absent"] + VERSIONS[61]["absent"]
VERSIONS[62]["phrases"] = VERSIONS[61]["phrases"] + [VERSIONS[61]["once"]] + VERSIONS[62]["phrases"]
VERSIONS[62]["absent"] = VERSIONS[61]["absent"] + VERSIONS[62]["absent"]
# Kept as names for the v30 tests and any caller that imported them.
BLOCK_LINE = 83
MUST_APPEAR_ONCE = VERSIONS[30]["once"]
PHRASES = [
    "to firm up later IF NEEDED",
    "we can pin the exact day later IF IT MATTERS",
    "to verify the exact days IF NEEDED",
    "Say the return as a fact, not a possibility",
    "if you can find it",                            # the counterweight
    "when you have your mail in front of you",       # the counterweight
    "A condition on WHETHER she has to come back is not",
    "por verificar",                                 # the anchor it follows
    "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM",  # the rule it must not cost
]


def _key() -> str:
    key = os.environ.get("CZ_ADMIN_KEY", "")
    if not key:
        raise SystemExit("CZ_ADMIN_KEY is empty in this container; refusing to guess")
    return key


def call(method: str, url: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Admin-Key": _key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def running_sha() -> str:
    with urllib.request.urlopen(HEALTH, timeout=15) as r:
        return json.loads(r.read().decode()).get("git_sha", "")


def deploy_has_landed(running: str, expected: str) -> tuple[bool, str]:
    """(may we sync, why not).

    Pure so it can be tested without a server. An empty running sha is NOT
    treated as a match: unreachable and unknown are different from equal, the
    same distinction #962 drew for the admin page's build badge.
    """
    if not expected:
        return False, "no --expect-sha given; refusing to sync blind"
    if not running:
        return False, "the running server did not report a git_sha"
    if not (running.startswith(expected) or expected.startswith(running)):
        # A mismatch is refused in BOTH directions. Older means the deploy has
        # not landed and a sync would push the old bundle. Newer means a later
        # merge deployed on top (2026-09-14: #977 landed between the /health
        # check and the sync); the bundle is probably right but "probably" is
        # not the standard, so confirm the running bundle's version and re-run
        # with the sha that is actually running.
        return False, ("the running image is %s, not %s. If the running image is "
                       "OLDER the deploy has not landed and syncing would push the "
                       "old bundle; if it is NEWER, confirm the running bundle's "
                       "version and re-run with --expect-sha %s"
                       % (running[:12], expected[:12], running[:12]))
    return True, ""


def served() -> tuple[dict, str]:
    _, doc = call("GET", "%s/%s" % (ADMIN, SLUG))
    body = doc.get("data", doc)
    return body, body.get("systemPrompt", "")


def verify(sp: str, version: int) -> bool:
    """Read the SERVED string back by phrase, for one version. Returns whether
    it is right. An unlisted version is a refusal, not a pass."""
    spec = VERSIONS.get(version)
    if spec is None:
        print("  *** no phrase list for version %s; add one before syncing ***" % version)
        return False
    ok = True

    count = sp.count(spec["once"])
    ok &= count == 1
    print("  %-52s %s" % (spec["once"][:52],
                          "once  OK" if count == 1 else "*** %d ***" % count))

    for phrase in spec["phrases"]:
        present = phrase in sp
        ok &= present
        print("  %-52s %s" % (phrase[:52], "OK" if present else "*** MISSING ***"))

    for phrase in spec.get("absent", []):
        count = sp.count(phrase)
        ok &= count == 0
        print("  %-52s %s" % (phrase[:52], "absent  OK" if count == 0
                              else "*** STILL PRESENT x%d ***" % count))

    # Present SOMEWHERE in the document is not the claim; it has to be in the
    # block. The block is located by its own text and the once phrase must
    # appear AFTER it, so a reflow cannot move the check onto the wrong line.
    start = sp.find(spec["block_anchor"])
    in_block = start != -1 and spec["once"] in sp[start:]
    detail = "OK" if in_block else ("*** BLOCK NOT FOUND ***" if start == -1 else "*** WRONG SECTION ***")
    ok &= in_block
    print("  %-52s %s" % ("inside the block after %r" % spec["block_anchor"][:20], detail))
    return bool(ok)


def max_tokens_ok(body: dict, expected: int | None) -> bool:
    """The served maxTokens, read back, against the value this run synced.

    A value change (2026-09-27, 2048 -> 6144 for the seven-trip truncation)
    has no phrase to read back, so the check is the number itself. None means
    this run did not sync maxTokens and there is nothing to check."""
    if expected is None:
        return True
    got = body.get("maxTokens")
    ok = got == expected
    print("  %-52s %s" % ("maxTokens == %d" % expected,
                          "OK" if ok else "*** SERVED %r ***" % (got,)))
    return ok


def sync_keys(max_tokens: int | None) -> list[str]:
    """Exactly the keys the sync sends. /version always rides along, so the
    served number names the bundle it came from rather than counting writes."""
    keys = ["/systemPrompt", "/version"]
    if max_tokens is not None:
        keys.append("/maxTokens")
    return keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--expect-sha", default="",
                    help="the merge commit carrying the prompt change. The sync "
                         "REFUSES unless the running image is that commit.")
    ap.add_argument("--force", action="store_true",
                    help="sync even if the running image is not --expect-sha. "
                         "Only for re-syncing text already in the running image.")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="also sync /maxTokens from the bundle and refuse unless "
                         "the served value reads back as exactly this number.")
    args = ap.parse_args(argv)

    running = ""
    try:
        running = running_sha()
    except (urllib.error.URLError, OSError) as exc:
        print("could not read /health: %s" % exc)

    may, why = deploy_has_landed(running, args.expect_sha)
    print("RUNNING %s   EXPECTED %s" % (running[:12] or "unknown",
                                        args.expect_sha[:12] or "none given"))
    if not may:
        if not args.force:
            print("\nREFUSING TO SYNC: %s" % why)
            return 2
        print("\n--force: syncing anyway (%s)" % why)

    body, sp = served()
    print("BEFORE  version=%s  chars=%d  maxTokens=%s" % (body.get("version"), len(sp), body.get("maxTokens")))

    status, rep = call("POST", "%s/%s/sync-from-bundle" % (ADMIN, SLUG),
                       # /version too, so the served number names the bundle it
                       # came from rather than counting writes. Without it v38
                       # served as "37", the shelved cut's number (2026-09-20).
                       {"keys": sync_keys(args.max_tokens)})
    print("SYNC    HTTP %s  version=%s" % (status, rep.get("version")))
    for c in rep.get("changes", []):
        print("    %-20s %s" % (c.get("key"), c.get("status")))

    body, sp = served()
    print("\nAFTER, read back from the running server by STRING")
    print("  version=%s  systemPrompt chars=%d" % (body.get("version"), len(sp)))
    ok = verify(sp, int(body.get("version") or 0))
    ok = max_tokens_ok(body, args.max_tokens) and ok

    print("\nRESULT:", "the served N-400 prompt carries the rule and its counterweight"
          if ok else "*** THE SERVED PROMPT IS NOT RIGHT, DO NOT CLOSE THIS OUT ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
