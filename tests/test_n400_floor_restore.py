"""The v44 evidence floor (agreed with the auditor 2026-09-28): a choice fact
cited from an EARLIER applicant line is set aside, and restored only when Jev
says her words support that option. Jev is faked here; the live acceptance is
qa/n400_floor_acceptance.py (jorge-r3 t25 and jorge-r5 t21 restored at
0.98/1.0/0.92, conf-v18 t47 kept out)."""
import asyncio
import json

import pytest

from app.services import n400_evidence_support as es
from app.services import typesafe_judge
from app.services.n400_interviewer_guard import drop_facts_without_current_evidence

CONV = "INTERVIEWER: Married?\nAPPLICANT: Nah, single. I got a little girl though, Valeria, she's with her mom, I pay child support.\n"
CATALOGUE = {"p6.child1.residence": ["resides_with_me", "not_with_me", "unknown_missing"],
             "p6.child1.supported": ["yes", "no"]}


def _turn(*facts):
    return json.dumps({"facts": [{"field_id": f, "value": v, "provenance": {"utterance": u}} for f, v, u in facts],
                       "deferred": [], "reply": {"en": "Got it."}})


def _floor(text, said="Just the one. Valeria Ramirez, born October 3rd, 2021."):
    return drop_facts_without_current_evidence(text, said, CONV)[0]


def _run(text, verdicts, mode="primary", monkeypatch=None):
    async def fake(api_key, state, questions, *, judgment, mode):
        return {"answers": {f"f{i}": v for i, v in enumerate(verdicts)}}, {"judgment": judgment}
    monkeypatch.setattr(typesafe_judge, "guarded_ask", fake)
    monkeypatch.setattr(typesafe_judge, "record_later", lambda row, app_id: None)
    return json.loads(asyncio.run(es.restore_supported_carried_facts(
        text, None, "t25", "probe", mode, "key", choice_fields=CATALOGUE)))


def test_a_carried_choice_fact_is_set_aside_not_just_dropped():
    out = json.loads(_floor(_turn(("p6.child1.residence", "not_with_me", "she's with her mom"))))
    assert out["facts"] == []
    assert out["facts_dropped"][0]["pending_support"] is True
    assert out["facts_pending_support"][0]["field_id"] == "p6.child1.residence"


def test_supported_is_restored_and_the_pending_key_never_reaches_the_wire(monkeypatch):
    text = _floor(_turn(("p6.child1.residence", "not_with_me", "she's with her mom"),
                        ("p6.child1.supported", "yes", "I pay child support")))
    out = _run(text, [{"choice": "supports", "confidence": 0.98}, {"choice": "supports", "confidence": 1.0}],
               monkeypatch=monkeypatch)
    assert {f["field_id"]: f["value"] for f in out["facts"]} == {"p6.child1.residence": "not_with_me",
                                                                 "p6.child1.supported": "yes"}
    assert "facts_pending_support" not in out and "facts_dropped" not in out
    assert all("_cited_line" not in f for f in out["facts"])


@pytest.mark.parametrize("verdict", [
    {"choice": "insufficient", "confidence": 0.9},   # conf-v18's shape
    {"choice": "supports", "confidence": 0.3},       # unsure is not evidence
])
def test_anything_short_of_confident_support_stays_dropped(verdict, monkeypatch):
    text = _floor(_turn(("p6.child1.supported", "yes", "I pay child support")))
    out = _run(text, [verdict], monkeypatch=monkeypatch)
    assert out["facts"] == [] and out["facts_dropped"][0]["field_id"] == "p6.child1.supported"
    assert "facts_pending_support" not in out


def test_off_or_shadow_mode_fails_closed_to_dropped(monkeypatch):
    text = _floor(_turn(("p6.child1.residence", "not_with_me", "she's with her mom")))
    out = _run(text, [{"choice": "supports", "confidence": 1.0}], mode="shadow", monkeypatch=monkeypatch)
    assert out["facts"] == [] and "facts_pending_support" not in out


def test_a_name_put_together_across_two_turns_is_kept():
    """Round 1's José: "José" in her earlier line, "Delgado" now."""
    conv = "APPLICANT: José. Nació en 1980, el día no lo sé.\n"
    t = json.dumps({"facts": [{"field_id": "p6.child3.child_name", "value": "José Delgado",
                               "provenance": {"utterance": "José. Nació en 1980"}}]})
    out, dropped = drop_facts_without_current_evidence(t, "Delgado. Vive en Guadalajara.", conv)
    assert dropped == [] and json.loads(out)["facts"][0]["value"] == "José Delgado"


# --- identifier digit count (priya-r7 t3) and probation never had (jorge-r6 t49)

from app.services.n400_interviewer_guard import (  # noqa: E402
    drop_identifiers_with_wrong_digit_count, file_probation_never_had,
)


def test_an_identifier_one_digit_short_is_dropped_with_a_reason():
    t = json.dumps({"facts": [{"field_id": "p2.uscis_account_number", "value": "20001111222"},
                              {"field_id": "p2.ssn", "value": "627184402"}]})
    out, dropped = drop_identifiers_with_wrong_digit_count(t)
    assert [f["field_id"] for f in json.loads(out)["facts"]] == ["p2.ssn"]
    assert dropped == [{"field_id": "p2.uscis_account_number", "digits": 11, "expected": [12],
                        "reason": "identifier has the wrong number of digits for its box"}]


PROB = ("q_p9_crimes_probation_completed | Part 9: Your record | p9.probation_completed | "
        "If you received a suspended sentence, were placed on probation, or were paroled, have you completed it? | options: yes, no\n")


@pytest.mark.parametrize("said,filed", [
    ("Never had any of that, just the ticket.", True),
    ("Nunca, nada de eso.", True),
    ("I never finished it, I'm still on it.", False),   # answers the question the other way
    ("Yes, I completed it in 2019.", False),
])
def test_probation_never_had_files_not_applicable_only_on_her_never(said, filed):
    t = json.dumps({"facts": [], "reply": {"en": "Understood."}})
    out, info = file_probation_never_had(t, PROB, said)
    got = [f for f in json.loads(out)["facts"] if f["field_id"] == "p9.probation_completed"]
    assert bool(got) is filed and (not got or got[0]["value"] == "")


def test_probation_is_left_alone_when_it_is_not_the_standing_question():
    t = json.dumps({"facts": [], "reply": {"en": "ok"}})
    other = PROB.replace("q_p9_crimes_probation_completed", "q_p9_other").replace("p9.probation_completed", "p9.has_arrest2")
    assert file_probation_never_had(t, other, "Never had any of that.")[1] is None


# --- v45: a settled trailing question is replaced (round 7 B) ----------------

from app.services.n400_interviewer_guard import _date_said, _state_paired, guard_response_text  # noqa: E402

AG = ("q_p2_country_of_birth | Part 2: About you | p2.country_of_birth | What is your country of birth?\n"
      "q_p2_nationality | Part 2: About you | p2.country_of_nationality | What is your country of citizenship or nationality?\n")


def _settled(cite, said, reply="Got it. What is your country of birth?", locale="en"):
    t = json.dumps({"facts": [{"field_id": "p2.country_of_birth", "value": "India", "provenance": {"utterance": cite}}],
                    "asking": {"node_id": "q_p2_country_of_birth", "field_ids": ["p2.country_of_birth"]},
                    "reply": {locale: reply}})
    return json.loads(guard_response_text(t, AG, "t3", user_content=said, locale=locale))


def test_a_question_about_a_node_this_response_settled_is_replaced_with_the_next():
    o = _settled("Chennai, India", "I was born in Chennai, India.")
    assert o["reply"]["en"] == "Got it. What is your country of citizenship or nationality?"
    assert o["asking"] == {"node_id": "q_p2_nationality", "field_ids": ["p2.country_of_nationality"]}
    assert o["asking_dropped"]["question_replaced_with"] == "q_p2_nationality"


def test_when_the_floor_drops_the_settling_fact_the_question_stays():
    o = _settled("born in Chennai, India", "I was born February 2 in Chennai, India.")
    assert o["reply"]["en"] == "Got it. What is your country of birth?"


def test_the_interview_locale_is_the_one_replaced():
    ag_es = AG.replace("What is your country of citizenship or nationality?", "¿De qué país es ciudadana?")
    t = json.dumps({"facts": [{"field_id": "p2.country_of_birth", "value": "Mexico", "provenance": {"utterance": "México"}}],
                    "asking": {"node_id": "q_p2_country_of_birth", "field_ids": ["p2.country_of_birth"]},
                    "reply": {"es": "Anotado, México. ¿En qué país nació?", "en": "Noted, Mexico. What country were you born in?"}})
    o = json.loads(guard_response_text(t, ag_es, "t3", user_content="Nací en México.", locale="es"))
    assert o["reply"]["es"] == "Anotado, México. ¿De qué país es ciudadana?"
    assert o["reply"]["en"] == "Noted, Mexico. What country were you born in?", "only the interview locale is spoken"


# --- v45: the floor's date equivalence and same-city state (round 7 C) -------

@pytest.mark.parametrize("value,said,ok", [
    ("2025-05-09", "May 9th to May 14th, 2025", True),
    ("2025-05-14", "May 9th to May 14th, 2025", True),
    ("2025-05-19", "May 9th to May 14th, 2025", False),   # a day she did not say
    ("2024-05-09", "May 9th to May 14th, 2025", False),   # a year she did not say
    ("2023-12", "salí en diciembre de 2023", True),
])
def test_a_normalized_date_is_her_words_when_month_day_and_year_are(value, said, ok):
    assert _date_said(value, said) is ok


LINES = ["yeah so right now i'm on rigsby, 4410 rigsby, san antonio, texas 78222", "all san antonio"]


@pytest.mark.parametrize("value,cited,ok", [
    ("TX", "All San Antonio", True),    # her own line paired San Antonio with Texas
    ("FL", "All San Antonio", False),   # a state she never said
    ("TX", "All Houston", False),       # a city she never paired with it
])
def test_a_state_is_her_words_only_when_she_paired_that_city_with_it(value, cited, ok):
    assert _state_paired({"field_id": "p4.prior_address1.state"}, value, cited, LINES) is ok


# --- v45: occupations (round 7 D) -------------------------------------------

from app.services.n400_interviewer_guard import fix_occupations  # noqa: E402


@pytest.mark.parametrize("value,expect", [
    ("Self-employed", None),                              # who employs him, not the work
    ("Self-employed, delivery driver", "Delivery driver"),
    ("Delivery driver", "Delivery driver"),
])
def test_occupation_is_the_work_never_self_employed(value, expect):
    t = json.dumps({"facts": [{"field_id": "p7.employer2.occupation", "value": value}]})
    got = {f["field_id"]: f["value"] for f in json.loads(fix_occupations(t, "")[0])["facts"]}
    assert got.get("p7.employer2.occupation") == expect


def test_a_current_idle_row_on_file_is_not_overwritten_by_a_job_word():
    known = "p7.employer1.occupation: Retired\np7.employer1.to: present\n"
    for value in ("Costurera, jubilada", "Costurera"):   # rosa-r7 t65's exact value, and a plain job word
        t = json.dumps({"facts": [{"field_id": "p7.employer1.occupation", "value": value}]})
        out = json.loads(fix_occupations(t, known)[0])
        assert out["facts"] == [] and out["facts_dropped"][0]["field_id"] == "p7.employer1.occupation", value
    same = json.dumps({"facts": [{"field_id": "p7.employer1.occupation", "value": "Jubilada"}]})
    assert json.loads(fix_occupations(same, known)[0])["facts"], "the idle word itself re-files fine"
