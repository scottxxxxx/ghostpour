"""v64 code floors (agreed with the auditor, 2026-10-17).

1. An identifier is filed with exactly the digits she said. Cases 0e731ec1
   and 766ab91c: ten digits said, a phone-shaped stand-in on the wire, NINE
   filed. Nine passes the count guard, so only a comparison with her cited
   digits can see the trim. A dropped identifier is re-asked, never read back.
2. A date given in two pieces across two answers (766ab91c t_014/t_015:
   "...since January 1st." then "2015.").
"""
from __future__ import annotations

import json

from app.services.n400_interviewer_guard import (
    drop_facts_without_current_evidence, drop_identifiers_not_as_said, guard_response_text,
)

AGENDA = ("q_p2_ssn | Part 2: About you | p2.ssn | What is your Social Security number?\n"
          "q_p2_lpr_date | Part 2: About you | p2.lpr_date | What date did you become a permanent resident?\n"
          "q_p1_a_number | Part 1: Eligibility | p1.a_number | What is your A-Number?\n")


def _fact(fid, value, utt):
    return {"field_id": fid, "value": value, "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": utt}}


def _turn(*facts, reply="Got it, 5 5 5 5 5 5 0 1 8 9, noted.", locale="en"):
    r = {"en": reply} if locale == "en" else {locale: reply, "en": reply}
    return json.dumps({"schema_version": 1, "turn_id": "t_016", "intent": {"type": "answer"},
                       "facts": list(facts), "deferred": [], "clarification": None, "conflict": None,
                       "escalation": None, "complete": True,
                       "asking": {"node_id": "q_p3_ethnicity", "field_ids": ["p3.ethnicity"]},
                       "section_checkpoint": None, "interview_over": False, "reply": r}, ensure_ascii=False)


def _ids(text):
    return [f["field_id"] for f in json.loads(text)["facts"]]


SAID_766 = ("Male, parents are not citizens, no disabilities. My social security number is "
            "555-555-0189 and I don't want a new social security card.")


# --- 1. the identifier floor ---------------------------------------------------

def test_766s_trimmed_ssn_is_dropped_and_re_asked_never_read_back():
    text = _turn(_fact("p2.ssn", "555550189", "My social security number is 555-555-0189"),
                 _fact("p2.gender", "male", "Male"))
    out = json.loads(guard_response_text(text, AGENDA, "t_016", user_content=SAID_766, locale="en"))
    assert [f["field_id"] for f in out["facts"]] == ["p2.gender"]
    assert out["reply"] == {"en": "Social Security numbers have 9 digits, I heard 10, could you say it again?"}
    assert "0189" not in json.dumps(out["reply"])
    assert out["asking"] == {"node_id": "q_p2_ssn", "field_ids": ["p2.ssn"]}
    assert out["section_checkpoint"] is None and out["identifier_reask"]["heard"] == 10


def test_an_ssn_filed_exactly_as_said_is_kept_and_the_reply_untouched():
    text = _turn(_fact("p2.ssn", "555447781", "my social is 555-44-7781"), reply="5 5 5, 4 4, 7 7 8 1, thanks.")
    out = json.loads(guard_response_text(text, AGENDA, "t_016", user_content="my social is 555-44-7781"))
    assert _ids(json.dumps(out)) == ["p2.ssn"] and out["reply"]["en"] == "5 5 5, 4 4, 7 7 8 1, thanks."


def test_ten_digits_filed_whole_go_to_the_count_guard_and_are_re_asked_too():
    text = _turn(_fact("p2.ssn", "5554447781", "555-444-7781"))
    out = json.loads(guard_response_text(text, AGENDA, "t_016", user_content="555-444-7781"))
    assert out["facts"] == [] and out["reply"]["en"].startswith("Social Security numbers have 9 digits, I heard 10")


def test_a_pad_is_dropped_and_re_asked_without_a_count_she_did_not_get_wrong():
    out, dropped = drop_identifiers_not_as_said(_turn(_fact("p1.a_number", "012345678", "A 1 2 3 4 5 6 7 8")))
    assert _ids(out) == [] and dropped[0]["heard"] == 8
    full = json.loads(guard_response_text(_turn(_fact("p1.a_number", "012345678", "A 1 2 3 4 5 6 7 8")),
                                          AGENDA, "t", user_content="A 1 2 3 4 5 6 7 8"))
    assert full["reply"]["en"] == "I want to be sure I have it exactly right. Could you say the A-Number again?"


def test_two_runs_are_never_merged():
    out, _ = drop_identifiers_not_as_said(_turn(_fact("p1.a_number", "123456789", "1234567 and 89")))
    assert _ids(out) == []


def test_digits_it_cannot_read_are_never_judged():
    out, dropped = drop_identifiers_not_as_said(_turn(_fact("p2.ssn", "555447781", "five five five four four")))
    assert _ids(out) == ["p2.ssn"] and dropped == []


def test_spanish_number_words_are_read_and_compared():
    said = "seis dos siete cuatro cuatro nueve cero uno ocho"
    ok, _ = drop_identifiers_not_as_said(_turn(_fact("p2.ssn", "627449018", said)), "es")
    bad, _ = drop_identifiers_not_as_said(_turn(_fact("p2.ssn", "62744901", said)), "es")
    assert _ids(ok) == ["p2.ssn"] and _ids(bad) == []


def test_the_re_ask_is_in_her_language():
    text = _turn(_fact("p2.ssn", "555550189", "mi seguro social es 555-555-0189"), locale="es")
    out = json.loads(guard_response_text(text, AGENDA, "t", user_content="mi seguro social es 555-555-0189", locale="es"))
    assert out["reply"]["es"] == "Los números de Seguro Social tienen 9 dígitos y escuché 10, ¿me lo puede repetir?"
    assert out["reply"]["en"].startswith("Social Security numbers have 9 digits")


def test_phones_are_left_out():
    out, dropped = drop_identifiers_not_as_said(_turn(_fact("p11.mobile_phone", "555550189", "555-555-0189")))
    assert _ids(out) == ["p11.mobile_phone"] and dropped == []


# --- 2. a date in two pieces ---------------------------------------------------

CONV = ("INTERVIEWER: What is your country of citizenship or nationality?\n"
        "APPLICANT: Mexico. I've been a permanent resident since January 1st.\n"
        "INTERVIEWER: What year did you become a permanent resident on January 1st?\n"
        "APPLICANT: 2015.")
SAID = "2015."
CITE = "I've been a permanent resident since January 1st. 2015."


def _date(value="2015-01-01", utt=CITE, conv=CONV, key=None, agenda=AGENDA, said=SAID):
    text = json.dumps({"facts": [{"field_id": "p2.lpr_date", "value": value, "value_type": "date",
                                  "provenance": {"source": "user_stated", "confidence": 0.95, "utterance": utt}}]})
    out, _ = drop_facts_without_current_evidence(text, said, conv, agenda=agenda, locale="en",
                                                 applicant_line_field_ids=key)
    return _ids(out)


def test_t_014_t_015s_shape_is_kept_without_the_key():
    assert _date() == ["p2.lpr_date"]


def test_with_the_key_either_line_may_carry_the_date_field():
    assert _date(key="1: p2.country_of_nationality\n2: p2.lpr_date") == ["p2.lpr_date"]
    assert _date(key="1: p2.lpr_date\n2: p2.ssn") == ["p2.lpr_date"]


def test_with_the_key_two_lines_for_other_fields_drop_it():
    assert _date(key="1: p2.country_of_nationality\n2: p2.ssn") == []


def test_without_the_key_a_field_off_the_agenda_drops_it():
    assert _date(agenda="q_p2_ssn | Part 2: About you | p2.ssn | What is your Social Security number?\n") == []


def test_a_missing_year_is_never_inferred():
    conv = CONV.replace("APPLICANT: 2015.", "APPLICANT: yes")
    assert _date(utt="I've been a permanent resident since January 1st. yes", conv=conv, said="yes") == []


def test_a_citation_that_is_not_her_words_drops_it():
    assert _date(utt="I've been a permanent resident since January 1st, 2015.") == []


def test_a_line_older_than_her_previous_answer_drops_it():
    conv = CONV.replace("APPLICANT: 2015.", "APPLICANT: Mexico too.\nINTERVIEWER: And the year?\nAPPLICANT: 2015.")
    assert _date(conv=conv) == []
