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
