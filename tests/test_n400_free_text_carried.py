"""v63: a CARRIED free-text fact filed in English (agreed with the auditor,
2026-10-16).

v63 files every free-text answer in English. The evidence floor keeps a fact
cited from an earlier line of hers only when the value is inside her cited
words (conf-v18), which a translation never is, so "House cleaning" carried
from "yo limpio casas" would be dropped where "Limpieza de casas" passed.

Accepted only for the free-text fields, only off English, and only where her
cited line answered THAT field: the app's `applicant_line_field_ids` (build
203) when it knows, else her immediately previous answer and a field still
open on the agenda. Everything else keeps the conf-v18 test.
"""
from __future__ import annotations

import json
from app.models.chat import ChatResponse
from app.services.n400_interviewer_guard import (
    drop_facts_without_current_evidence, is_free_text_field,
)

AGENDA = ("q_p7_job1 | Part 7: Your work | p7.employer1.employer_name,p7.employer1.occupation,"
          "p7.employer1.from | ¿Dónde trabaja y cuál es su trabajo?\n")
CONV = ("INTERVIEWER: ¿Dónde trabaja ahora y cuál es su trabajo ahí?\n"
        "APPLICANT: yo limpio casas, eso cuenta como trabajo\n"
        "INTERVIEWER: Sí cuenta. ¿Trabaja para una empresa o por su cuenta?\n"
        "APPLICANT: por mi cuenta, para mí misma")
SAID = "por mi cuenta, para mí misma"


def _fact(field_id="p7.employer1.occupation", value="House cleaning", utt="yo limpio casas"):
    return {"field_id": field_id, "value": value, "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": utt}}


def _floor(fact=None, *, locale="es", key=None, agenda=AGENDA, conv=CONV):
    text = json.dumps({"facts": [fact or _fact()]}, ensure_ascii=False)
    out, dropped = drop_facts_without_current_evidence(
        text, SAID, conv, agenda=agenda, locale=locale, applicant_line_field_ids=key)
    return [f["field_id"] for f in json.loads(out)["facts"]], dropped


def test_the_free_text_fields_are_exactly_the_auditors_list():
    for fid in ("p7.employer1.occupation", "p7.employer2.employer_name", "p9.arrested.explain",
                "p9.offense1.offense", "p9.offense1.outcome", "p9.offense1.sentence",
                "p9.offense1.reason", "p1.eligibility_other"):
        assert is_free_text_field(fid), fid
    for fid in ("p7.employer1.city", "p7.employer1.from", "p2.gender", "p1.a_number",
                "p2.current_legal_name.last_name", "p1.eligibility_basis"):
        assert not is_free_text_field(fid), fid


def test_said_in_turn_n_filed_in_english_in_n_plus_1_is_kept_when_her_line_answered_it():
    kept, dropped = _floor(key="1: p7.employer1.occupation\n2: p7.employer1.employer_name")
    assert kept == ["p7.employer1.occupation"] and dropped == []


def test_the_same_value_on_a_different_free_text_field_is_dropped():
    kept, dropped = _floor(_fact(field_id="p7.employer1.employer_name"),
                           key="1: p7.employer1.occupation\n2: p7.employer1.employer_name")
    assert kept == [] and dropped[0]["field_id"] == "p7.employer1.employer_name"


def test_an_english_interview_keeps_the_conf_v18_test():
    kept, _ = _floor(locale="en", key="1: p7.employer1.occupation\n2: p7.employer1.employer_name")
    assert kept == []


def test_a_choice_field_keeps_the_conf_v18_test():
    kept, _ = _floor(_fact(field_id="p2.gender", value="female"),
                     key="1: p2.gender\n2: p7.employer1.employer_name")
    assert kept == []


def test_without_the_key_her_previous_answer_and_an_open_field_is_the_floor():
    kept, _ = _floor()
    assert kept == ["p7.employer1.occupation"]


def test_unknown_lines_and_a_count_that_does_not_line_up_fall_back_to_the_floor():
    assert _floor(key="1: unknown\n2: unknown")[0] == ["p7.employer1.occupation"]
    assert _floor(key="1: p7.employer1.employer_name")[0] == ["p7.employer1.occupation"]


def test_the_floor_drops_a_field_the_agenda_no_longer_holds():
    other = "q_p9_x | Part 9: Your record | p9.claimed_citizen | ¿Alguna vez...?\n"
    assert _floor(agenda=other)[0] == []


def test_the_floor_drops_a_line_older_than_her_previous_answer():
    conv = CONV + "\nINTERVIEWER: ¿Desde cuándo?\nAPPLICANT: " + SAID
    conv = conv.replace("APPLICANT: por mi cuenta, para mí misma\nINTERVIEWER: ¿Desde",
                        "APPLICANT: desde dos mil dieciocho\nINTERVIEWER: ¿Desde")
    assert _floor(conv=conv)[0] == []


# --- on the wire: the router hands the app's key to the floor ------------------

ENVELOPE = {
    "schema_version": 1, "turn_id": "t-ft-1", "intent": {"type": "answer"},
    "facts": [_fact()], "deferred": [], "clarification": None, "conflict": None,
    "escalation": None, "complete": False,
    "asking": {"node_id": "q_p7_job1", "field_ids": ["p7.employer1.from"]},
    "section_checkpoint": None, "interview_over": False,
    "reply": {"es": "Anotado, limpia casas por su cuenta. ¿Desde cuándo?", "en": "Noted. Since when?"},
}


def _post(client, free_user, mock_provider, key):
    from tests.conftest import chat_request
    md = {"call_type": "n400_interviewer_turn", "form_code": "N-400", "jurisdiction": "US",
          "locale": "es", "turn_id": "t-ft-1", "conversation": CONV,
          "known_facts": "p2.country_of_birth = Mexico", "agenda": AGENDA,
          "applicant_line_field_ids": key}
    body = chat_request(system_prompt="", user_content=SAID, metadata=md)
    canned = ChatResponse(text=json.dumps(ENVELOPE, ensure_ascii=False), input_tokens=100,
                          output_tokens=50, model="claude-sonnet-5", provider="anthropic",
                          usage={"input_tokens": 100, "output_tokens": 50})
    mock_provider.return_value = canned
    r = client.post("/v1/chat", json=body, headers={**free_user["headers"], "X-App-ID": "n400"})
    assert r.status_code == 200, r.text
    return [f["field_id"] for f in json.loads(r.json()["text"])["facts"]]


def test_the_key_reaches_the_floor_through_the_router(client, free_user, mock_provider):
    # Without the key the floor (her previous answer, an open field) would
    # KEEP this fact, so a drop here proves the router passed the key.
    assert _post(client, free_user, mock_provider, "1: p7.employer1.employer_name\n2: p7.employer1.employer_name") == []
    assert _post(client, free_user, mock_provider, "1: p7.employer1.occupation\n2: p7.employer1.employer_name") == [
        "p7.employer1.occupation"]
