"""A name SPELLED in one turn and filed in the next (case 0e731ec1, agreed with
the auditor 2026-10-16).

t_008 she spelled "E V A N D E R, but"; t_009 she said "That's the 1st and
last name. There was no middle name." and the lane filed
p2.other_name2_last_name "Evander" citing her spelling. The floor's word test
needs the word "evander" in her line, which holds only single letters, so it
dropped the fact and the lane asked for the last name three more times.
"""
from __future__ import annotations

import json

from app.services.n400_interviewer_guard import drop_facts_without_current_evidence

SPELLED = "E V A N D E R, but"
SAID = "That's it. That's the 1st and last name. There was no middle name."


def _conv(spelled=SPELLED):
    return ("INTERVIEWER: What was the other name?\nAPPLICANT: Seymour\n"
            "INTERVIEWER: Sorry, go ahead, you said Seymour, and then?\nAPPLICANT: " + spelled + "\n"
            "INTERVIEWER: So Seymour Evander, is that first and last, or is there more?\nAPPLICANT: " + SAID)


def _kept(field_id="p2.other_name2_last_name", value="Evander", utt=None, spelled=SPELLED):
    fact = {"field_id": field_id, "value": value, "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": utt or spelled.split(",")[0]}}
    out, _ = drop_facts_without_current_evidence(json.dumps({"facts": [fact]}), SAID, _conv(spelled))
    return [f["field_id"] for f in json.loads(out)["facts"]]


def test_t_009s_shape_is_kept():
    assert _kept() == ["p2.other_name2_last_name"]


def test_hyphens_and_periods_spell_too():
    assert _kept(spelled="E-V-A-N-D-E-R, but") == ["p2.other_name2_last_name"]
    assert _kept(value="Eva", spelled="e. v. a.", utt="e. v. a.") == ["p2.other_name2_last_name"]


def test_a_run_one_letter_short_drops():
    assert _kept(spelled="E V A N D E, but", utt="E V A N D E") == []


def test_a_run_with_an_extra_letter_drops():
    assert _kept(spelled="E V A N D E R S, but", utt="E V A N D E R S") == []


def test_a_run_spelling_a_different_name_drops():
    assert _kept(value="Evans") == []


def test_the_same_run_on_a_free_text_or_choice_field_drops():
    assert _kept(field_id="p7.employer1.employer_name") == []
    assert _kept(field_id="p2.gender", value="evander") == []


def test_a_two_word_value_passes_only_when_each_word_does():
    # "Seymour" is spoken in an earlier line, "Evander" is spelled in the cited one.
    assert _kept(field_id="p2.other_name2_first_name", value="Seymour Evander",
                 spelled="Seymour E V A N D E R", utt="Seymour E V A N D E R") == ["p2.other_name2_first_name"]
    assert _kept(field_id="p2.other_name2_first_name", value="Seymour Evans",
                 spelled="Seymour E V A N D E R", utt="Seymour E V A N D E R") == []
