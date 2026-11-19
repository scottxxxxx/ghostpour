"""A date already on file for ANOTHER field is confirmed, never silently
filed (envelope trim sim, auditor ruling 2026-11-19)."""
from __future__ import annotations

import json

from app.services.n400_interviewer_guard import confirm_value_on_other_field, guard_response_text

AGENDA = ("q_p4_current_address | Part 4: Addresses | p4.current_address.street,p4.current_address.city,"
          "p4.current_address.from | What is your current address and when did you move in?")
KNOWN = "p1.date_of_birth: 1987-09-28\np2.lpr_date: 2019-06-15\np2.ssn: on file"


def _turn(value="2019-06-15", fid="p4.current_address.from", extra=()):
    facts = [{"field_id": fid, "value": value, "value_type": "date",
              "provenance": {"source": "ai_extracted", "confidence": 0.8, "utterance": "June 15, 2019"}}]
    facts += list(extra)
    return json.dumps({"schema_version": 1, "turn_id": "t1", "intent": {"type": "partial_answer"},
                       "facts": facts, "deferred": [], "complete": False,
                       "asking": {"node_id": "q_p4_current_address", "field_ids": ["p4.current_address.street"]},
                       "section_checkpoint": None, "interview_over": False,
                       "reply": {"en": "June 15th, 2019 for when you moved in. What's your street?"}})


def test_a_date_equal_to_another_fields_date_becomes_a_confirm():
    out, hit = confirm_value_on_other_field(_turn(), AGENDA, KNOWN)
    t = json.loads(out)
    assert hit == {"field_id": "p4.current_address.from", "value": "2019-06-15", "other_field_id": "p2.lpr_date"}
    assert t["facts"] == []
    assert t["reply"]["en"] == "June 15, 2019 is also your green card date. Is that the date for this one too?"
    assert t["asking"] == {"node_id": "q_p4_current_address", "field_ids": ["p4.current_address.from"]}


def test_only_the_matching_fact_is_taken_back():
    street = {"field_id": "p4.current_address.street", "value": "2176 Chickadee Parkway",
              "provenance": {"utterance": "2176 Chickadee Parkway"}}
    t = json.loads(confirm_value_on_other_field(_turn(extra=[street]), AGENDA, KNOWN)[0])
    assert [f["field_id"] for f in t["facts"]] == ["p4.current_address.street"]


def test_a_different_date_or_the_same_field_passes_untouched():
    for text in (_turn(value="2022-03-01"), _turn(fid="p2.lpr_date")):
        out, hit = confirm_value_on_other_field(text, AGENDA, KNOWN)
        assert hit is None and out == text


def test_her_yes_to_the_confirm_files_it_no_loop():
    conv = ("INTERVIEWER: June 15, 2019 is also your green card date. Is that the date for this one too?\n"
            "APPLICANT: yes")
    out, hit = confirm_value_on_other_field(_turn(), AGENDA, KNOWN, conv)
    assert hit is None and out == _turn()


def test_spanish_reply_gets_a_spanish_confirm():
    t = json.loads(_turn()); t["reply"] = {"es": "x"}
    out, _ = confirm_value_on_other_field(json.dumps(t), AGENDA, KNOWN)
    assert json.loads(out)["reply"]["es"].startswith("El 15 de junio de 2019 también es la fecha de su tarjeta verde")


def test_wired_into_the_full_guard_chain():
    out = guard_response_text(_turn(), AGENDA, "t1", user_content="Like I told you before, June 15, 2019",
                              conversation="INTERVIEWER: What's your street and city?\nAPPLICANT: hmm",
                              known_facts=KNOWN, locale="en")
    t = json.loads(out)
    assert t.get("value_on_other_field", {}).get("other_field_id") == "p2.lpr_date"
    assert not t["facts"]
