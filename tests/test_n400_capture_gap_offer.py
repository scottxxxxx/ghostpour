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


def test_a_day_not_asked_yet_line_is_asked_and_a_dont_know_re_sends_the_deferral():
    i = _PROMPT.index('"partial date, day not asked yet, ask for the exact day once"')
    rule = _PROMPT[i:i + 700]
    assert "Ask for that day now, as the next question" in rule
    assert "both ends of one row in one question is fine" in rule
    assert "re-send the deferral with the same field id and the same partial" in rule
