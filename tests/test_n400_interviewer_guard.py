"""The stale-asking backstop, and the two cases it must leave alone."""

import json

from app.services.n400_interviewer_guard import (
    DROP_REASON, agenda_field_ids, drop_stale_asking, guard_response_text,
)

AGENDA = "\n".join([
    "q_p1_eligibility_basis | Part 1: Your eligibility | p1.eligibility_basis | Why are you eligible? | options: general_provision, spouse_usc",
    "q_p1_a_number | Part 1: Your eligibility | p1.a_number | What is your A-Number?",
    "q_p2_full_name | Part 2: Your name | p2.first_name, p2.middle_name, p2.last_name | What is your full legal name?",
    "q_p9_crimes | Part 9: Additional information | p9.arrested_ever | Have you ever been arrested, cited or detained?",
])


def _turn(asking_node, facts, extra=None):
    t = {"schema_version": 1, "turn_id": "t_004", "intent": "answer", "facts": facts,
         "deferred": [], "clarification": None, "conflict": None, "escalation": None,
         "complete": True,
         "asking": {"node_id": asking_node, "field_ids": []} if asking_node else None,
         "section_checkpoint": None, "interview_over": False,
         "reply": {"en": "Got it. What is your A-Number?"}}
    if extra:
        t.update(extra)
    return json.dumps(t)


def _fact(fid):
    return {"field_id": fid, "value": "x", "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": "x"}}


def test_agenda_lines_parse_to_field_id_sets():
    ids = agenda_field_ids(AGENDA)
    assert ids["q_p2_full_name"] == {"p2.first_name", "p2.middle_name", "p2.last_name"}
    assert ids["q_p1_eligibility_basis"] == {"p1.eligibility_basis"}


def test_the_spanish_defect_shape_is_dropped_and_marked():
    """conf-es-v14 turn 4: the basis minted, asking still names its node
    while the reply asks the A-Number."""
    out, info = drop_stale_asking(_turn("q_p1_eligibility_basis", [_fact("p1.eligibility_basis")]), AGENDA)
    t = json.loads(out)
    assert t["asking"] is None
    assert t["asking_dropped"] == {"node_id": "q_p1_eligibility_basis",
                                   "field_ids": ["p1.eligibility_basis"], "reason": DROP_REASON}
    assert info is not None


def test_a_partial_answer_keeps_asking_on_the_node():
    """Two of three name fields minted, the reply asks for the middle name:
    asking legitimately stays on the node. The auditor's first exception."""
    text = _turn("q_p2_full_name", [_fact("p2.first_name"), _fact("p2.last_name")])
    out, info = drop_stale_asking(text, AGENDA)
    assert out == text and info is None


def test_a_node_absent_from_the_agenda_is_left_alone():
    text = _turn("q_p9_arrest_row_1", [_fact("p9.arrested_ever")])
    out, info = drop_stale_asking(text, AGENDA)
    assert out == text and info is None


def test_no_agenda_means_no_guard():
    text = _turn("q_p1_eligibility_basis", [_fact("p1.eligibility_basis")])
    assert drop_stale_asking(text, None) == (text, None)


def test_non_json_and_null_asking_pass_through_byte_for_byte():
    assert drop_stale_asking("not json {", AGENDA) == ("not json {", None)
    text = _turn(None, [_fact("p1.eligibility_basis")])
    assert drop_stale_asking(text, AGENDA) == (text, None)


def test_a_bare_string_asking_is_not_touched_here():
    """A v10 regression shape the prompt forbids; this guard is not the
    place to repair it, and must not crash on it."""
    text = _turn(None, [_fact("p1.eligibility_basis")], {"asking": "q_p1_eligibility_basis"})
    assert drop_stale_asking(text, AGENDA) == (text, None)


def test_the_route_helper_returns_the_rewritten_text():
    out = guard_response_text(_turn("q_p1_eligibility_basis", [_fact("p1.eligibility_basis")]), AGENDA, "t_004")
    assert json.loads(out)["asking"] is None


def test_the_route_calls_the_guard_for_this_call_type_only():
    src = open("app/routers/chat.py").read()
    i = src.index("guard_response_text(")
    assert 'body.get_meta("call_type") == "n400_interviewer_turn"' in src[i - 600:i]


# --- the evidence floor ------------------------------------------------------

def _turn_with_facts(facts):
    return json.dumps({"schema_version": 1, "intent": "answer", "facts": facts, "asking": None,
                       "reply": {"en": "x"}})


def _pf(fid, utt):
    return {"field_id": fid, "value": "yes", "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": utt}}


def test_turn_47_shape_is_dropped_and_marked():
    """conf-v18 turn 47: a yes to the summary minted the standing line's one
    empty id with the PRIOR turn's words as evidence."""
    from app.services.n400_interviewer_guard import drop_facts_without_current_evidence, EVIDENCE_DROP_REASON
    out, dropped = drop_facts_without_current_evidence(
        _turn_with_facts([_pf("p6.child1.supported", "she's my daughter, my own, I had her")]), "yes")
    t = json.loads(out)
    assert t["facts"] == [] and t["facts_dropped"][0]["field_id"] == "p6.child1.supported"
    assert dropped[0]["reason"] == EVIDENCE_DROP_REASON


def test_a_fact_quoting_the_current_words_survives_case_and_spacing():
    from app.services.n400_interviewer_guard import drop_facts_without_current_evidence
    text = _turn_with_facts([_pf("p11.email", "no email,  I don't have one")])
    out, dropped = drop_facts_without_current_evidence(text, "I'm sorry, I gave that already, and No email, I don't have one")
    assert dropped == [] and out == text


def test_a_fact_with_no_utterance_is_dropped():
    from app.services.n400_interviewer_guard import drop_facts_without_current_evidence
    f = _pf("p1.a_number", ""); f["provenance"]["utterance"] = ""
    out, dropped = drop_facts_without_current_evidence(_turn_with_facts([f]), "A 1 2 3")
    assert len(dropped) == 1 and json.loads(out)["facts"] == []


def test_the_floor_leaves_non_json_alone():
    from app.services.n400_interviewer_guard import drop_facts_without_current_evidence
    assert drop_facts_without_current_evidence("prose", "yes") == ("prose", [])


def test_the_route_passes_the_raw_utterance_captured_before_assembly():
    src = open("app/routers/chat.py").read()
    assert src.index("_n400_utterance = body.user_content") < src.index("# 2.5. Server-side prompt assembly")
    assert 'user_content=body.get_meta("user_input") or _n400_utterance' in src
