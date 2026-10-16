"""A capture gap with her words is offered back, never asked cold (v62).

Build 188, Scott's device: "I am a carpenter at Alamo Builders now" said at
the birth-country ask became a capture gap, and Part 7 opened with "where do
you work or study right now?" as if she had said nothing. v62 tells the lane
to offer her words back and, on her yes, mint each field with her yes as the
utterance, the same as any offer. That instruction is only safe if GP's own
evidence floor keeps such a fact, so this pins it.
"""

import json
from pathlib import Path

from app.services.n400_interviewer_guard import drop_facts_without_current_evidence

_PROMPT = json.loads((Path(__file__).parent.parent / "config" / "remote" / "n400"
                      / "interviewer-turn.json").read_text(encoding="utf-8"))["systemPrompt"]


def _turn(*facts):
    return json.dumps({"facts": [
        {"field_id": fid, "value": v, "value_type": "string",
         "provenance": {"source": "user_stated", "confidence": 0.95, "utterance": utt}}
        for fid, v, utt in facts]})


def test_a_yes_to_the_offered_employer_keeps_both_fields():
    text = _turn(("p7.employer1.employer_name", "Alamo Builders", "yes"),
                 ("p7.employer1.occupation", "carpenter", "yes"))
    out, dropped = drop_facts_without_current_evidence(text, "yes, still there")
    assert dropped == []
    assert [f["value"] for f in json.loads(out)["facts"]] == ["Alamo Builders", "carpenter"]


def test_a_yes_cited_fact_is_still_dropped_when_she_did_not_say_yes():
    """The floor is not waived: the cited yes must be in THIS line."""
    text = _turn(("p7.employer1.employer_name", "Alamo Builders", "yes"))
    _, dropped = drop_facts_without_current_evidence(text, "no, I left there in May")
    assert [d["field_id"] for d in dropped] == ["p7.employer1.employer_name"]


def test_the_prompt_offers_the_partial_back_at_a_section_boundary():
    assert "A CAPTURE GAP WITH HER WORDS IS OFFERED BACK, NEVER ASKED COLD" in _PROMPT
    i = _PROMPT.index("A CAPTURE GAP WITH HER WORDS IS OFFERED BACK")
    rule = _PROMPT[i:i + 900]
    assert "SECTION BOUNDARY" in rule
    assert "with her yes as its provenance.utterance" in rule


def test_volunteer_fields_is_described_as_the_window_the_client_sends():
    """Since DECISIONS 2026-09-14 the client sends every open field before
    Part 9 (InterviewerEnvelope.volunteerFieldIDs, mirrored by the N-400
    repo's qa/n400_qa.py volunteer_fields_text). Describing it as "the
    current part and the next" told the lane a Part 7 id was out of view in
    Part 2, which is how Alamo Builders became a capture gap."""
    assert "for the current part and the next." not in _PROMPT
    assert ("every open field of every part before Part 9, and Part 9's own "
            "only while you are standing in Part 9.") in _PROMPT


def test_a_later_part_value_said_early_is_two_facts_not_one_capture_gap():
    i = _PROMPT.index("A LATER PART'S VALUE SAID EARLY IS MINTED")
    rule = _PROMPT[i:i + 600]
    assert 'p7.employer1.employer_name "Alamo Builders"' in rule
    assert 'p7.employer1.occupation "carpenter"' in rule
    assert "never one capture gap holding both" in rule
    # A capture gap is now only a value with no id on EITHER list.
    assert ("because no field id for it is in the agenda or in VOLUNTEER FIELDS, "
            "is a CAPTURE GAP") in _PROMPT


def test_a_gap_question_answer_is_a_row_never_a_gate_no():
    i = _PROMPT.index("A GAP QUESTION THE APP WROTE IS ASKED AS WRITTEN")
    rule = _PROMPT[i:i + 700]
    assert '"I was out of work then" is an unemployed row, never has_job no' in rule
    assert '"I was staying with family" is an address row, never has_prior_address no' in rule


def test_an_accepted_stretch_goes_down_the_month_and_year_path():
    """The app does not fill a gap row's dates (historyGapAsk only words the
    line). Her "yeah, between those" gives months and years, which the
    standing rule never mints as a "2021-11" fact: ask the day, defer the
    month only if she does not know."""
    i = _PROMPT.index("THE STRETCH IN THE APP'S QUESTION IS AN ANCHOR OFFERED")
    rule = _PROMPT[i:i + 700]
    assert "the month and year rule applies: mint no date, ask the start day once" in rule
    assert 'defer with the month as the partial ("2021-11") only if she does not know' in rule
    # The rule it leans on is still there and still forbids a month-only fact.
    assert 'never a fact with the value "2017-10" either' in _PROMPT


def test_the_reply_never_credits_her_with_checking_she_did_not_say():
    """Case 9e332c5d, build 190: the old checkpoint example itself read
    "which you are checking", and the summary credited him with a plan he
    never stated."""
    assert "which you are checking" not in _PROMPT
    i = _PROMPT.index("NEVER SAY OR IMPLY SHE IS CHECKING")
    rule = _PROMPT[i:i + 500]
    assert '"except the exact start and end days at Lone Star Homes"' in rule
    assert "which we'll come back to" in rule


def test_a_date_capture_gap_with_a_partial_asks_the_day_and_never_offers_the_year():
    """Build 191 widens the build 147 green card path: a same-turn YYYY or
    YYYY-MM deferral on a p4/p7 date arrives as a capture gap. The offer-back
    rule must not turn "partial 2017" into a yes and a minted "2017"."""
    i = _PROMPT.index("A CAPTURE GAP ON A DATE WHOSE PARTIAL IS A YEAR OR A MONTH AND YEAR")
    assert i > _PROMPT.index("A CAPTURE GAP WITH HER WORDS IS OFFERED BACK")
    rule = _PROMPT[i:i + 800]
    assert "never offer the partial back for a yes and never mint it as the date" in rule
    assert "ask for the exact day next" in rule
    assert "or the green card date p2.lpr_date" in rule
    assert "both ends of one row in one question is fine" in rule
    assert 're-send the deferral with the same field id and the same partial, origin "applicant"' in rule
    assert "day not asked yet" not in _PROMPT


def test_asking_field_ids_lists_only_what_the_question_asks():
    """Build 191 counts a field as asked only when asking.field_ids lists it.
    A ZIP question listing the whole job row would read as the dates asked."""
    i = _PROMPT.index("`field_ids` LISTS ONLY THE FIELDS YOUR SPOKEN QUESTION ASKS")
    assert i > _PROMPT.index('"asking": null, or an OBJECT {"node_id": string, "field_ids": [string]}')
    rule = _PROMPT[i:i + 600]
    assert 'lists only ["p7.employer2.postal_code"]' in rule
    assert '["p7.employer2.from", "p7.employer2.to"]' in rule


def test_asking_node_id_owns_its_fields_never_the_gate_just_answered():
    """t_023's first call named the has_job2 gate with the job row's field;
    an older client read node_id alone and discarded the reply unspoken."""
    i = _PROMPT.index("AND `node_id` IS THE NODE THAT OWNS THOSE FIELDS")
    rule = _PROMPT[i:i + 500]
    assert '{"node_id": "q_p7_job2", "field_ids": ["p7.employer2.from"]}' in rule
    assert "never q_p7_more_jobs1" in rule


# --- build 193 (case c2ba1f75): the close, put-offs, days, checkpoints -------

def test_the_close_says_the_form_is_complete_and_asks_nothing():
    i = _PROMPT.index("THE END OF THE INTERVIEW: THE SWEEP, THEN THE CLOSE.")
    rule = _PROMPT[i:i + 1600]
    assert "say the form is complete, ask nothing, and set `interview_over` true" in rule
    assert "NEVER ASK WHETHER SHE WANTS TO GO THROUGH HER ANSWERS OR GO STRAIGHT TO THE FORM" in rule
    assert "Would you like to go through your answers" not in _PROMPT
    # The sweep still comes before the close.
    assert "asks the sweep question below and NOT the close" in rule
    i = _PROMPT.index('"interview_over": boolean')
    assert "true on the close (the agenda empty and the sweep done or not needed)" in _PROMPT[i:i + 300]


def test_a_field_is_deferred_only_on_her_own_put_off():
    i = _PROMPT.index("A FIELD IS DEFERRED ONLY ON HER OWN PUT-OFF")
    rule = _PROMPT[i:i + 900]
    assert "is ASKED NEXT, never deferred and never \"left open\"" in rule
    assert "I'll leave the ZIP open for now" in rule
    assert "ask for the missing piece once" in rule
    # The line that taught a same-turn partial deferral is gone.
    assert "or gives only part of one (a year, a season, a month without a day), do not mint" not in _PROMPT
    assert "the exact dates are asked next, one row at a time, and never deferred with the country" in _PROMPT


def test_a_date_is_said_back_with_the_day_she_gave():
    i = _PROMPT.index("A DATE IS SAID BACK AS SHE GAVE IT, THE DAY INCLUDED")
    rule = _PROMPT[i:i + 400]
    assert 'never "January 2017 to February 2022"' in rule


def test_every_part_summary_carries_its_checkpoint():
    i = _PROMPT.index("EVERY PART SUMMARY CARRIES ITS `section_checkpoint`")
    assert "Parts 1, 2 and 5 were summed up in words only" in _PROMPT[i:i + 500]


def test_a_part_is_finished_before_the_next_one_opens():
    """Scott's standing rule (case c2ba1f75, build 193): Part 4 closed with a
    ZIP owed, Part 2's middle name came after Part 4, and Tamarack's ZIP was
    asked after Part 7."""
    i = _PROMPT.index("FINISH A PART BEFORE MOVING ON (Scott, 2026-10-16")
    assert i > _PROMPT.index("MOVING THE INTERVIEW")
    rule = _PROMPT[i:i + 1400]
    assert "the ZIP, the day of each date and every agenda line of that part" in rule
    assert "only when she explicitly asks to move on or says she will come back to it later" in rule
    assert "you may ask ONCE whether she would like to come back to it later, and move only on her yes" in rule
    assert "Never go back to a part you already summarized to ask something it owed" in rule
    # The agenda paragraph no longer licenses a jump to any node.
    assert "You may ask any node on the agenda when the conversation makes that natural." not in _PROMPT
    assert "You may ask any node of the part you are in" in _PROMPT


def test_a_volunteered_none_files_its_gate_no_in_the_same_response():
    """Case 53328dc8, build 194, t_006: "no middle name" was said back and
    never filed, and the middle name question came back."""
    i = _PROMPT.index('A STATED NONE THAT ANSWERS A GATE IS THAT GATE\'S "no"')
    rule = _PROMPT[i:i + 1200]
    assert 'p2.has_middle_name "no", p2.has_other_names "no"' in rule
    assert 'for "no kids" the count p6.total_children 0' in rule
    assert "even when the gate belongs to a later part" in rule
    assert "NEVER SAY BACK A VALUE, OR A NONE, THAT THIS RESPONSE'S facts OR KNOWN FACTS DO NOT CARRY." in rule
    # The empty-string rule no longer claims the gated nones.
    assert "no email, no second phone, no middle name, no other names, mint that field" not in _PROMPT
    assert "A STATED NONE on an OPTIONAL field with no gate is a fact" in _PROMPT
