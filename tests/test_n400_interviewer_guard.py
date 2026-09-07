"""The stale-asking backstop, and the two cases it must leave alone."""

import json
import pytest

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


def test_a_node_absent_from_the_agenda_is_dropped_and_marked_off_agenda():
    """conf-v19 English 60: asking named a node the agenda did not list and
    nothing was minted for it, so the all-fields rule could not see it."""
    from app.services.n400_interviewer_guard import OFF_AGENDA_REASON
    text = _turn("q_p9_selective_service", [])
    out, info = drop_stale_asking(text, AGENDA)
    t = json.loads(out)
    assert t["asking"] is None and t["asking_dropped"]["reason"] == OFF_AGENDA_REASON
    assert info["node_id"] == "q_p9_selective_service"


def test_no_agenda_at_all_still_means_no_guard():
    text = _turn("q_p9_selective_service", [])
    assert drop_stale_asking(text, "") == (text, None)


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
    """Every guard_response_text call must sit inside an `if` that tests the
    call type, or the N-400 guards would run on ShoulderSurf traffic.

    Checked with the AST rather than by looking a fixed number of
    characters backwards: that heuristic passed for months and then broke
    the moment an unrelated block was inserted between the condition and
    the call, while the property it was meant to check was still true. A
    test that fails on a correct change is as much a defect as one that
    passes on a wrong one.
    """
    import ast

    tree = ast.parse(open("app/routers/chat.py").read())

    def calls_guard(node):
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "guard_response_text"
                   for n in ast.walk(node))

    def tests_call_type(node):
        return any(isinstance(n, ast.Constant) and n.value == "n400_interviewer_turn"
                   for n in ast.walk(node))

    guarded, total = 0, 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "guard_response_text":
            total += 1
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and tests_call_type(node.test) and calls_guard(node):
            guarded += sum(
                1 for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "guard_response_text")

    assert total >= 1, "the route no longer calls the guard at all"
    assert guarded == total, (
        f"{total - guarded} guard_response_text call(s) are not inside an "
        f"`if` that tests call_type == n400_interviewer_turn")


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



# --- a field is a fact or a deferral, never both ---------------------------

def test_turn_38_shape_keeps_the_deferral_and_drops_the_invented_day():
    from app.services.n400_interviewer_guard import BOTH_REASON, drop_facts_that_are_also_deferred
    turn = {"facts": [_pf("p4.prior_address1.from", "October twenty seventeen"), _pf("p4.current_address.city", "Dallas")],
            "deferred": [{"field_id": "p4.prior_address1.from", "reason": "day", "partial_value": "2017-10"}],
            "reply": {"en": "x"}}
    out, dropped = drop_facts_that_are_also_deferred(json.dumps(turn))
    t = json.loads(out)
    assert [f["field_id"] for f in t["facts"]] == ["p4.current_address.city"]
    assert t["deferred"][0]["partial_value"] == "2017-10"
    assert dropped == [{"field_id": "p4.prior_address1.from", "value": "yes", "reason": BOTH_REASON}]
    assert t["facts_dropped"][0]["reason"] == BOTH_REASON


def test_no_overlap_means_no_change():
    from app.services.n400_interviewer_guard import drop_facts_that_are_also_deferred
    text = json.dumps({"facts": [_pf("a", "x")], "deferred": [{"field_id": "b"}], "reply": {"en": "x"}})
    assert drop_facts_that_are_also_deferred(text) == (text, [])


def test_the_route_helper_runs_the_both_check_too():
    from app.services.n400_interviewer_guard import guard_response_text
    turn = {"facts": [_pf("a", "hello there")], "deferred": [{"field_id": "a", "partial_value": "h"}], "asking": None, "reply": {"en": "x"}}
    out = guard_response_text(json.dumps(turn), "", "t", user_content="hello there")
    assert json.loads(out)["facts"] == []


# --- the battery shortfall marker -------------------------------------------

_CRIMES = ("q_p9_crimes | Part 9: Additional information | "
           "p9.arrested_ever,p9.prostitution,p9.drugs,p9.polygamy,p9.marriage_fraud,"
           "p9.smuggling,p9.gambling,p9.child_support,p9.benefit_fraud,p9.child_hostilities | "
           "Have you ever been arrested, cited, detained, or charged, or committed a crime you were "
           "not arrested for? Or been involved in prostitution, illegal drugs, being married to two "
           "people at once, marrying for immigration papers, helping someone enter the U.S. illegally, "
           "illegal gambling, not paying child support or alimony, or lying to get a public benefit? "
           "If none of these apply, just say no.")


def _battery_turn(reply, ids):
    return json.dumps({"facts": [{"field_id": i, "value": "no",
                                  "provenance": {"utterance": "no never not even a ticket"}} for i in ids],
                       "reply": {"en": reply}})


def test_conf_v22_turn_67_shape_is_marked():
    """Ten fields minted from a question that spoke one clause."""
    from app.services.n400_interviewer_guard import mark_battery_shortfall
    ids = [f.strip() for f in _CRIMES.split("|")[2].split(",")]
    short = "Have you ever been arrested, cited, detained, or charged? If none apply, just say no."
    out, info = mark_battery_shortfall(_battery_turn(short, ids), _CRIMES)
    assert info is not None and info["node_id"] == "q_p9_crimes"
    assert len(info["minted"]) == 10 and info["spoken_chars"] < info["question_chars"]
    assert json.loads(out)["battery_unspoken"]["reason"].startswith("the spoken question was far shorter")
    # the facts are NOT dropped: which items were spoken cannot be told mechanically
    assert len(json.loads(out)["facts"]) == 10


def test_a_battery_spoken_in_full_is_not_marked():
    from app.services.n400_interviewer_guard import mark_battery_shortfall
    ids = [f.strip() for f in _CRIMES.split("|")[2].split(",")]
    full = _CRIMES.split("|")[3].strip()
    text = _battery_turn(full, ids)
    assert mark_battery_shortfall(text, _CRIMES) == (text, None)


def test_a_short_answer_on_a_small_node_is_never_marked():
    from app.services.n400_interviewer_guard import mark_battery_shortfall
    text = _battery_turn("And your A-Number?", ["p1.a_number"])
    assert mark_battery_shortfall(text, AGENDA) == (text, None)


def test_the_route_helper_marks_the_battery_too():
    from app.services.n400_interviewer_guard import guard_response_text
    ids = [f.strip() for f in _CRIMES.split("|")[2].split(",")]
    out = guard_response_text(_battery_turn("Ever been arrested? Just say no.", ids),
                              _CRIMES, "t_067", user_content="no never not even a ticket")
    assert json.loads(out)["battery_unspoken"]["node_id"] == "q_p9_crimes"


def test_a_composite_name_ask_is_not_a_battery_and_is_not_marked():
    """conf-v23 marked q_p1_full_name twice: "Ana Lucia Torres" legitimately
    mints three DIFFERENT values from a short reply. A battery answered in
    one word mints ONE value many times; that is the difference, and it
    compares the model's own values so it holds in every locale."""
    from app.services.n400_interviewer_guard import mark_battery_shortfall
    line = ("q_p2_full_name | Part 2: Your name | p2.first_name,p2.middle_name,p2.last_name | "
            "What is your full current legal name, first, middle and last, as it appears on your document?")
    turn = json.dumps({"facts": [
        {"field_id": "p2.first_name", "value": "Ana"},
        {"field_id": "p2.middle_name", "value": "Lucia"},
        {"field_id": "p2.last_name", "value": "Torres"}],
        "reply": {"en": "And your full legal name?"}})
    assert mark_battery_shortfall(turn, line) == (turn, None)


def test_a_uniform_no_across_a_battery_is_still_marked():
    from app.services.n400_interviewer_guard import mark_battery_shortfall
    ids = [f.strip() for f in _CRIMES.split("|")[2].split(",")]
    turn = json.dumps({"facts": [{"field_id": i, "value": "no"} for i in ids],
                       "reply": {"en": "Ever been arrested? Just say no."}})
    out, info = mark_battery_shortfall(turn, _CRIMES)
    assert info is not None and len(info["minted"]) == len(ids)


# --- conf-v24 turn 35: a day nobody spoke ----------------------------------

def _resp(**kw):
    base = {"intent": "answer", "facts": [], "deferred": [], "reply": "ok"}
    base.update(kw)
    return json.dumps(base)


def test_a_day_the_applicant_never_said_becomes_a_deferral():
    """The real conf-v24 turn 35 shape: a month and a year in, two
    day-precision dates out. The prompt has forbidden this since v13 and
    the lane did it anyway, in the same run where turn 32 got it right."""
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    text = _resp(facts=[
        {"field_id": "p4.prior_address1.from", "value": "2017-10-19"},
        {"field_id": "p4.prior_address1.to", "value": "2020-06-01"},
    ])
    out, moved = defer_dates_with_unspoken_day(
        text, "I moved in around October 2017 and left in June 2020")
    turn = json.loads(out)
    assert turn["facts"] == [], "no invented day may survive as a fact"
    assert {d["field_id"] for d in turn["deferred"]} == {
        "p4.prior_address1.from", "p4.prior_address1.to"}
    assert {d["partial_value"] for d in turn["deferred"]} == {"2017-10", "2020-06"}
    assert len(moved) == 2


@pytest.mark.parametrize("said", [
    "I moved in on October 19, 2017",        # digits
    "it was the nineteenth of October 2017",  # English ordinal
    "me mudé el diecinueve de octubre de 2017",  # Spanish word
    "el 19 de octubre",                       # Spanish digits
])
def test_a_day_she_actually_said_is_left_alone(said):
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    out, moved = defer_dates_with_unspoken_day(
        _resp(facts=[{"field_id": "x", "value": "2017-10-19"}]), said)
    assert moved == [], f"the day is right there in {said!r}"
    assert json.loads(out)["facts"][0]["value"] == "2017-10-19"


def test_the_year_is_not_read_as_a_day():
    """2017 must not satisfy a day of 17, or every date passes."""
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    _, moved = defer_dates_with_unspoken_day(
        _resp(facts=[{"field_id": "x", "value": "2017-10-17"}]), "October 2017")
    assert len(moved) == 1


def test_a_month_precision_value_is_not_touched():
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    _, moved = defer_dates_with_unspoken_day(
        _resp(facts=[{"field_id": "x", "value": "2017-10"}]), "October 2017")
    assert moved == []


# --- conf-v24 turn 80: a yes she did not give ------------------------------

def test_facts_minted_on_a_non_answer_are_MARKED_and_not_dropped():
    """This started life as a dropper and the auditor's data killed it
    before it shipped: across nine graded runs it would have dropped
    thirteen facts, and thirteen of the thirteen quote her current
    utterance, which the evidence floor had already vouched for. Zero true
    drops. The mislabel is real and worth counting; the facts are hers."""
    from app.services.n400_interviewer_guard import mark_facts_minted_on_a_non_answer

    # The exact shape from v17/v20/v22: an answer with a question wrapped
    # round it. "just Mariana, she's grown, she lives with me, does she count"
    text = _resp(intent="question_back", facts=[
        {"field_id": "p6.total_children", "value": "1"},
        {"field_id": "p6.child1.name", "value": "Mariana"},
        {"field_id": "p6.child1.residence", "value": "resides_with_me"},
    ])
    out, info = mark_facts_minted_on_a_non_answer(text)
    turn = json.loads(out)
    assert len(turn["facts"]) == 3, "her three real facts must survive"
    assert info["intent"] == "question_back"
    assert turn["minted_on_non_answer"]["minted"] == [
        "p6.child1.name", "p6.child1.residence", "p6.total_children"]


def test_the_oath_turn_is_marked_too():
    """conf-v24 turn 80, the case the dropper was built for. It is still
    counted; what protects her is the gloss, because no server-side check
    can manufacture an informed answer."""
    from app.services.n400_interviewer_guard import mark_facts_minted_on_a_non_answer

    _, info = mark_facts_minted_on_a_non_answer(_resp(
        intent="help_explain",
        facts=[{"field_id": f"p9.oath.{k}", "value": "yes"} for k in
               ("support_constitution", "bear_arms", "noncombatant")]))
    assert info is not None and len(info["minted"]) == 3


@pytest.mark.parametrize("intent", ["answer", "partial_answer", "volunteered_extra",
                                    "correction", "control"])
def test_intents_that_legitimately_carry_facts_are_not_marked(intent):
    from app.services.n400_interviewer_guard import mark_facts_minted_on_a_non_answer

    _, info = mark_facts_minted_on_a_non_answer(
        _resp(intent=intent, facts=[{"field_id": "a", "value": "yes"}]))
    assert info is None


def test_both_new_guards_are_actually_reached_by_the_orchestrator():
    """A guard nobody calls is decoration. This is the only test that
    proves guard_response_text runs them, which is what production uses."""
    from app.services.n400_interviewer_guard import guard_response_text

    out = guard_response_text(
        _resp(intent="help_explain",
              facts=[{"field_id": "p9.oath.bear_arms", "value": "yes",
                      "provenance": {"utterance": "I don't understand that part"}}]),
        None, "t-80", "I don't understand that part")
    turn = json.loads(out)
    assert turn["facts"], "the marker must not drop what the floor vouched for"
    assert turn["minted_on_non_answer"]["intent"] == "help_explain"

    out = guard_response_text(
        _resp(facts=[{"field_id": "p4.prior_address1.from", "value": "2017-10-19",
                      "provenance": {"utterance": "around October 2017"}}]),
        None, "t-35", "I moved in around October 2017")
    turn = json.loads(out)
    assert turn["facts"] == []
    assert turn["deferred"][0]["partial_value"] == "2017-10"


def test_a_bare_string_reply_does_not_take_the_turn_down():
    """guard_response_text runs on every response and nothing catches for
    it, so a model that returns `reply` as a string instead of the
    locale-keyed object must not raise. This was live until the non-answer
    rule stopped emptying `facts`, which had been hiding the line behind
    an early return."""
    from app.services.n400_interviewer_guard import guard_response_text

    out = guard_response_text(
        json.dumps({"intent": "answer",
                    "facts": [{"field_id": "a", "value": "1",
                               "provenance": {"utterance": "one"}}],
                    "reply": "a bare string, not an object"}),
        None, "t-x", "she said one")
    assert json.loads(out)["facts"], "the response must survive intact"

    for weird in (None, 42, ["a"]):
        guard_response_text(
            json.dumps({"intent": "answer",
                        "facts": [{"field_id": "a", "value": "1",
                                   "provenance": {"utterance": "one"}}],
                        "reply": weird}), None, "t-x", "she said one")


@pytest.mark.parametrize("value,said", [
    ("2017-10-21", "me mudé el veintiún de octubre"),   # apocopated
    ("2017-10-21", "el veintiuno de octubre"),
    ("2017-10-26", "el veintiséis de octubre"),          # accented
    ("2017-10-23", "el veintitrés de octubre"),
    ("2017-10-01", "el primero de octubre"),
    ("2017-10-15", "el quince de octubre"),
])
def test_spanish_day_words_as_they_are_actually_spoken(value, said):
    """"veintiún" is how this is said before a noun, and it folds to
    "veintiun", not "veintiuno". Without the apocopated forms a day she
    really gave would be deferred as if it were invented, which is the
    guard doing harm in the language it was least tested in."""
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    _, moved = defer_dates_with_unspoken_day(
        _resp(facts=[{"field_id": "x", "value": value}]), said)
    assert moved == [], f"{said!r} contains the day of {value}"


def test_a_bare_string_reply_is_normalized_at_the_gateway():
    """GP owns this contract, so GP fixes the shape rather than leaving every
    client to defend itself. The N-400 client's decoder threw typeMismatch on
    this wire and surfaced it as a NON-RETRYABLE error: a terminal failure
    mid-interview. Hardening both sides was necessary and not sufficient."""
    from app.services.n400_interviewer_guard import guard_response_text

    out = guard_response_text(
        json.dumps({"intent": "answer", "facts": [], "reply": "Y su número de A?"}),
        None, "t-x", "one")
    turn = json.loads(out)
    assert turn["reply"] == {"en": "Y su número de A?"}, (
        "the line she was sent must still be spoken, under the key every "
        "locale falls back to")
    assert turn["reply_shape_normalized"]["chars"] == len("Y su número de A?")


def test_a_proper_locale_object_is_left_exactly_alone():
    from app.services.n400_interviewer_guard import normalize_reply_shape

    text = json.dumps({"reply": {"en": "hi", "es": "hola"}})
    out, info = normalize_reply_shape(text)
    assert info is None and out == text


@pytest.mark.parametrize("weird", [None, 42, ["a"], {"en": 1}])
def test_a_reply_that_is_neither_string_nor_object_is_not_invented(weird):
    """Tolerating a string must not slide into manufacturing a reply out of
    an integer. These are counted elsewhere and passed through untouched."""
    from app.services.n400_interviewer_guard import normalize_reply_shape

    _, info = normalize_reply_shape(json.dumps({"reply": weird}))
    assert info is None


@pytest.mark.parametrize("day,said", [
    (31, "el treinta y uno de octubre"),   # the one the single-word map missed
    (31, "el treinta y un de octubre"),
    (21, "el veintiún de octubre"),
    (21, "el veinte y uno de octubre"),
    (29, "el veintinueve de octubre"),
    (24, "el veinticuatro de octubre"),
    (31, "on the thirty first of October"),
    (21, "on the twenty first of October"),
    (21, "on the twenty-first of October"),
    (28, "on the twenty-eighth of October"),
])
def test_every_compound_day_form_from_21_to_31(day, said):
    """The first pass at this covered 21 and missed 31, because
    "treinta y uno" is three tokens and the single-word map cannot see it.
    Partial coverage of a range is how "veintiun" got missed, so the phrase
    table is generated rather than typed."""
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    _, moved = defer_dates_with_unspoken_day(
        _resp(facts=[{"field_id": "x", "value": f"2017-10-{day:02d}"}]), said)
    assert moved == [], f"{said!r} contains day {day}"


@pytest.mark.parametrize("day", [1, 19, 21, 31])
def test_a_day_still_deferred_when_only_a_month_was_spoken(day):
    """The phrase table must not turn the guard off."""
    from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day

    _, moved = defer_dates_with_unspoken_day(
        _resp(facts=[{"field_id": "x", "value": f"2017-10-{day:02d}"}]),
        "en octubre de 2017")
    assert len(moved) == 1


# --- carry-forward across a clarification (2026-09-07) ----------------------
#
# The evidence floor assumed her evidence is in the turn that mints it. The
# CLARIFICATION PATH breaks that, and it is the lane at its best: she says
# "Tran Minh", the lane refuses to guess whether that is surname-first, asks,
# she answers, and the lane mints the name citing her original words.
#
# Measured live before the fix: both name fields dropped, and the applicant
# reached turn 25 holding a date of birth, country, nationality, gender,
# height and weight and NO NAME. 87 of 1997 fact-minting turns lost at least
# one fact this way.
#
# Every case below is a REAL logged turn, not a constructed shape.

_CLARIFY_CONVO = (
    "INTERVIEWER: What is your full legal name?\n"
    "APPLICANT: Tran Minh.\n"
    "INTERVIEWER: Got it, Tran Minh. Is that your first and last name, and do "
    "you have a legal middle name?\n"
    "APPLICANT: I don't know, what is middle name? I don't understand.\n"
    "INTERVIEWER: A middle name is an extra given name printed between your "
    "first and last name on an ID. Do you have one?\n"
)


def _floor(facts, said, convo=None):
    import json as _j
    from app.services.n400_interviewer_guard import drop_facts_without_current_evidence
    out, dropped = drop_facts_without_current_evidence(
        _j.dumps({"reply": {"en": "x"}, "facts": facts}), said, convo)
    return [f["field_id"] for f in _j.loads(out).get("facts", [])]


def test_a_name_carried_across_a_clarification_survives():
    """v14 turn 4, on the wire. Without this the applicant has no name."""
    kept = _floor(
        [{"field_id": "p1.first_name", "value": "Tran",
          "provenance": {"utterance": "Tran Minh"}},
         {"field_id": "p1.last_name", "value": "Minh",
          "provenance": {"utterance": "Tran Minh"}},
         {"field_id": "p2.has_middle_name", "value": "no",
          "provenance": {"utterance": "No, I don't have that."}}],
        "Oh, ok. No, I don't have that.", _CLARIFY_CONVO)
    assert sorted(kept) == ["p1.first_name", "p1.last_name", "p2.has_middle_name"]


def test_the_case_the_floor_was_built_for_is_still_refused():
    """⚠ conf-v18 turn 47, and the reason a conversation-wide match was
    REJECTED. She said "yes" to a summary and the lane minted
    p6.child1.supported citing "she's my daughter, my own, I had her", which
    is genuinely her sentence and says nothing about support.

    If this ever goes green the widening has become unsafe."""
    kept = _floor(
        [{"field_id": "p6.child1.supported", "value": "yes",
          "provenance": {"utterance": "she's my daughter, my own, I had her"}}],
        "yes",
        "APPLICANT: she's my daughter, my own, I had her\nINTERVIEWER: summary\n")
    assert kept == []


def test_the_lane_cannot_authorise_itself_from_an_interviewer_line():
    """Only APPLICANT lines count. Citing the interviewer's own text would
    make the floor check the lane against itself, which is not a floor."""
    kept = _floor(
        [{"field_id": "p4.current_address.city", "value": "Dallas",
          "provenance": {"utterance": "Dallas, Texas"}}],
        "yes",
        "INTERVIEWER: I have Dallas, Texas for your city.\nAPPLICANT: yes\n")
    assert kept == []


def test_a_normalised_value_carried_forward_is_still_dropped():
    """The value must appear in the words cited for it, so a date rendered
    1977-10-05 from "5 October 1977" fails. Conservative, and identical to
    the behaviour before this change, so it cannot regress anything."""
    kept = _floor(
        [{"field_id": "p1.date_of_birth", "value": "1977-10-05",
          "provenance": {"utterance": "I born 5 October 1977"}}],
        "no middle name", "APPLICANT: I born 5 October 1977\n")
    assert kept == []


def test_carry_forward_needs_the_conversation_to_be_supplied():
    """No conversation, old behaviour exactly. A caller that does not pass it
    loses nothing it had."""
    kept = _floor(
        [{"field_id": "p1.first_name", "value": "Tran",
          "provenance": {"utterance": "Tran Minh"}}],
        "Oh, ok.", None)
    assert kept == []


def test_a_same_breath_spoken_date_is_untouched_by_the_value_test():
    """⚠ THE FENCE ON THE OTHER SIDE. The value test applies ONLY to the
    carried-forward path. If it were applied to the turn-local path too,
    every spoken date in the product would be refused, because a normalised
    2001-02-11 is not inside "she was born February eleven two thousand one".

    conf-v18 t46, the turn immediately before the case this floor exists
    for. The auditor hit exactly this when widening their own copy and
    warned us; checking it here rather than reasoning about it.

    test_the_case_the_floor_was_built_for_is_still_refused fences the change
    from the loose side. This one fences it from the tight side. A guard
    with only one fence gets tightened into a regression by the next person
    trying to be careful."""
    kept = _floor(
        [{"field_id": "p6.child1.date_of_birth", "value": "2001-02-11",
          "provenance": {"utterance": "she was born February eleven two thousand one"}}],
        "Mariana Torres, she was born February eleven two thousand one, "
        "she's my daughter, my own, I had her",
        "APPLICANT: Mariana Torres, she was born February eleven two thousand "
        "one, she's my daughter, my own, I had her\nINTERVIEWER: summary\n")
    assert kept == ["p6.child1.date_of_birth"]
