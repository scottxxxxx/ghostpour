"""The N-400 evidence SUPPORT check: a quote being in her words is not the
same as her words establishing the value (production turn t_008).

No test here touches the network; `typesafe_judge.ask` is replaced. Whether
Jev judges WELL is `qa/jev_evidence_support_eval.py`'s question. What is under
test here is ours: which facts are sent, what is sent about them, that a
confident "not established" becomes a marker and nothing else does, and that
every failure leaves the response byte for byte as it was.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services import n400_evidence_support as es
from app.services import typesafe_judge as tj

AGENDA = (
    "q_p1_eligibility_basis | Part 1: Your eligibility | p1.eligibility_basis | First, how long have you had your green card, and did you get it through a U.S. citizen spouse or on your own? | options: general_provision, spouse_usc, vawa, other\n"
    "q_p1_a_number | Part 1: Your eligibility | p1.a_number | What is your 9-digit A-Number?\n"
    "q_p2_other_names_gate | Part 2: About you | p2.has_other_names | Have you used any other names since birth? | options: yes, no\n"
)
SAID = "Well, I've had it for 5 years and a lawyer helped me get it."


def _fact(field_id, value, utterance):
    return {"field_id": field_id, "value": value, "source": "applicant",
            "provenance": {"utterance": utterance}}


def _turn(*facts, **extra):
    return json.dumps({"schema_version": 1, "turn_id": "t_008", "facts": list(facts),
                       "deferred": [], "reply": {"en": "Got it."}, **extra}, ensure_ascii=False)


T008 = _turn(_fact("p1.eligibility_basis", "general_provision", "I've had it for 5 years"))


def _body(*choices):
    return {"answers": {f"f{i}": {"type": "choice", "choice": c, "confidence": conf}
                        for i, (c, conf) in enumerate(choices)},
            "usage": {"input_tokens": 600}}


@pytest.fixture(autouse=True)
def recorded(monkeypatch):
    rows = []

    async def fake_record(row, app_id):
        rows.append((dict(row), app_id))

    monkeypatch.setattr(tj, "record", fake_record)
    monkeypatch.setattr(tj, "breaker", tj.Breaker())
    # `_LIVE` is module state and a task in it belongs to the event loop of
    # the test that made it. One left behind is gathered by a LATER test on a
    # different loop ("The future belongs to a different loop"), which is how
    # a route test here broke fourteen unrelated tests on CI's Python 3.12
    # while the same order passed on a local 3.14. Production has one loop.
    tj._LIVE.clear()
    yield rows
    tj._LIVE.clear()


async def _drain():
    for _ in range(200):
        if not tj._LIVE:
            break
        await asyncio.gather(*list(tj._LIVE), return_exceptions=True)
        await asyncio.sleep(0)
    assert not tj._LIVE


async def _mark(text, mode="primary", key="k", agenda=AGENDA, said=SAID):
    return await es.mark_unsupported_enum_facts(text, agenda, said, "t_008", "n400", mode, key)


# --- which facts are sent ---------------------------------------------------------

def test_an_enum_fact_whose_value_is_not_in_the_cited_words_is_checked():
    (c,) = es.enum_facts_to_check(json.loads(T008), AGENDA, SAID)
    assert c["field_id"] == "p1.eligibility_basis" and c["recorded_answer"] == "general_provision"
    assert c["options"] == ["general_provision", "other", "spouse_usc", "vawa"]
    assert c["question"].startswith("First, how long have you had your green card")
    assert c["applicant_said"] == SAID and c["cited_words"] == "I've had it for 5 years"


def test_a_number_a_name_or_a_date_is_never_sent():
    turn = json.loads(_turn(_fact("p1.a_number", "204881367", "A 204 881 367")))
    assert es.enum_facts_to_check(turn, AGENDA, "A 204 881 367") == []


def test_a_yes_that_is_in_the_cited_words_is_already_carried_by_the_floor():
    turn = json.loads(_turn(_fact("p2.has_other_names", "yes", "Yes, my maiden name")))
    assert es.enum_facts_to_check(turn, AGENDA, "Yes, my maiden name") == []


def test_a_no_inferred_from_other_words_is_checked():
    turn = json.loads(_turn(_fact("p2.has_other_names", "no", "People call me Beto")))
    assert [c["field_id"] for c in es.enum_facts_to_check(turn, AGENDA, "People call me Beto")] == ["p2.has_other_names"]


def test_a_value_outside_the_declared_options_is_not_this_checks_business():
    turn = json.loads(_turn(_fact("p1.eligibility_basis", "five_years", "five years")))
    assert es.enum_facts_to_check(turn, AGENDA, "five years") == []


def test_no_facts_no_agenda_or_no_options_sends_nothing():
    assert es.enum_facts_to_check(json.loads(_turn()), AGENDA, SAID) == []
    assert es.enum_facts_to_check(json.loads(T008), None, SAID) == []
    assert es.enum_facts_to_check(json.loads(T008), "q | Part | p1.eligibility_basis | Why?\n", SAID) == []


# --- what is sent ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_state_is_the_question_her_words_and_the_value_and_never_the_case(monkeypatch):
    seen = {}

    async def fake_ask(api_key, state, questions, timeout=tj.TIMEOUT_SECONDS):
        seen.update(state=state, questions=questions, timeout=timeout)
        return _body(("supports", 0.99))

    monkeypatch.setattr(tj, "ask", fake_ask)
    await _mark(_turn(_fact("p1.eligibility_basis", "general_provision", "I've had it for 5 years"),
                      known_facts_echo="p1.a_number = 204881367"))
    assert list(seen["state"]) == ["facts"] and len(seen["state"]["facts"]) == 1
    assert set(seen["state"]["facts"][0]) == {"field_id", "question", "applicant_said",
                                              "cited_words", "recorded_answer", "options"}
    assert "204881367" not in json.dumps(seen["state"])
    assert list(seen["questions"]) == ["f0"] and "`facts[0].question`" in seen["questions"]["f0"]["instructions"]
    assert seen["timeout"] == tj.PRIMARY_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_two_enum_facts_are_one_request(monkeypatch):
    ask = AsyncMock(return_value=_body(("supports", 0.9), ("insufficient", 0.9)))
    monkeypatch.setattr(tj, "ask", ask)
    out = json.loads(await _mark(_turn(
        _fact("p1.eligibility_basis", "general_provision", "on my own"),
        _fact("p2.has_other_names", "no", "People call me Beto")), said="on my own. People call me Beto"))
    assert ask.await_count == 1
    assert [u["field_id"] for u in out["facts_unsupported"]] == ["p2.has_other_names"]


# Each clause below moved a measured case (qa/jev_support_variants.py): the
# hedge and the inference took the real eligibility misses from 1 in 4 to 3
# in 4, and dropping "a different matter" lost the synthetic kids-live-with-me
# case 5 of 5. A rewrite that loses one must go back through both evals.
@pytest.mark.parametrize("clause", [
    "only part of a question with several parts",
    "fits more than one option",
    "worked out from a detail",
    "a guess from something she said about a different matter",
    "does not know or is not sure",
    "repeats what someone else told her",
])
def test_insufficient_keeps_every_clause_the_evals_measured(clause):
    for q in es.support_questions(2).values():
        assert clause in q["criteria"]["insufficient"]["what"]


# --- what becomes a marker ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_t008_a_confident_insufficient_marks_and_keeps_the_fact(monkeypatch, recorded):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_body(("insufficient", 0.83))))
    out = json.loads(await _mark(T008))
    assert out["facts_unsupported"] == [{
        "field_id": "p1.eligibility_basis", "value": "general_provision",
        "verdict": "insufficient", "confidence": 0.83, "reason": es.UNSUPPORTED_REASON}]
    assert out["facts"] == json.loads(T008)["facts"], "it marks, it never drops"
    await _drain()
    assert [(r["judgment"], r["mode"], r["outcome"], a) for r, a in recorded] == [
        (es.JUDGMENT, "primary", "ok", "n400")]


@pytest.mark.parametrize("verdict", ["contradicts", "unrelated"])
@pytest.mark.asyncio
async def test_the_other_not_established_verdicts_mark_too(monkeypatch, verdict):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_body((verdict, 0.9))))
    assert json.loads(await _mark(T008))["facts_unsupported"][0]["verdict"] == verdict


@pytest.mark.asyncio
async def test_supports_is_byte_for_byte(monkeypatch):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_body(("supports", 0.99))))
    assert await _mark(T008) == T008


@pytest.mark.asyncio
async def test_an_unsure_jev_marks_nothing(monkeypatch):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_body(("insufficient", tj.CONFIDENCE_FLOOR - 0.01))))
    assert await _mark(T008) == T008
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_body(("insufficient", tj.CONFIDENCE_FLOOR))))
    assert "facts_unsupported" in json.loads(await _mark(T008))


# --- fail open --------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,key", [("off", "k"), ("primary", ""), ("on", "k")])
@pytest.mark.asyncio
async def test_off_no_key_or_an_undefined_mode_calls_nothing(monkeypatch, mode, key):
    ask = AsyncMock(return_value=_body(("insufficient", 0.99)))
    monkeypatch.setattr(tj, "ask", ask)
    assert await _mark(T008, mode=mode, key=key) == T008
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_a_jev_failure_leaves_the_text_alone_and_counts_toward_the_shared_breaker(monkeypatch, recorded):
    ask = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(tj, "ask", ask)
    for _ in range(4):
        assert await _mark(T008) == T008
    await _drain()
    assert ask.await_count == 3, "the fourth call must find the breaker open"
    assert [r["outcome"] for r, _ in recorded] == ["error", "error", "error", "breaker_open"]


@pytest.mark.asyncio
async def test_prose_or_a_non_object_passes_through(monkeypatch):
    ask = AsyncMock(return_value=_body(("insufficient", 0.99)))
    monkeypatch.setattr(tj, "ask", ask)
    assert await _mark("not json at all") == "not json at all"
    assert await _mark("[1, 2]") == "[1, 2]"
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_a_bug_inside_the_check_cannot_fail_the_turn(monkeypatch):
    monkeypatch.setattr(es, "enum_facts_to_check", lambda *a: (_ for _ in ()).throw(KeyError("bug")))
    assert await _mark(T008) == T008


# --- shadow --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shadow_counts_and_never_marks_and_never_waits(monkeypatch, recorded):
    release = asyncio.Event()

    async def hung(*a, **k):
        await release.wait()
        return _body(("insufficient", 0.99))

    monkeypatch.setattr(tj, "ask", hung)
    out = await asyncio.wait_for(_mark(T008, mode="shadow"), timeout=1.0)
    assert out == T008
    assert tj._LIVE, "the judgment should still be running after the text came back"
    release.set()
    await _drain()
    assert [(r["mode"], r["outcome"]) for r, _ in recorded] == [("shadow", "ok")]


# --- the wire ---------------------------------------------------------------------------------------

def test_the_route_runs_it_after_the_deterministic_guards():
    src = open("app/routers/chat.py").read()
    guard = src.index("response.text = guard_response_text(")
    check = src.index("response.text = await mark_unsupported_enum_facts(")
    assert 0 < check - guard < 1500, "the support check must judge the facts that SURVIVED the guards"


# --- through the real route, both paths ---------------------------------------------------------------

def _route_envelope():
    from tests.test_n400_sentence_stream_route import ENVELOPE
    env = dict(ENVELOPE, asking={"node_id": "q_p1_a_number", "field_ids": ["p1.a_number"]})
    env["facts"] = [{"field_id": "p1.eligibility_basis", "value": "general_provision",
                     "source": "applicant", "provenance": {"utterance": "I've had it for 5 years"}}]
    return env


def test_the_marker_reaches_the_wire_on_the_json_path_and_the_stream_path(client, free_user, monkeypatch):
    from unittest.mock import patch
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata, _stream_of
    monkeypatch.setattr("app.services.document_generation._typesafe_mode", lambda: ("primary", "k"))
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_body(("insufficient", 0.83))))
    text = json.dumps(_route_envelope(), ensure_ascii=False)
    hdr = {**free_user["headers"], "X-App-ID": "n400"}

    async def fake(provider_router, request, db, settings):
        return ChatResponse(text=text, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                            provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", headers=hdr, json=chat_request(
            system_prompt="", user_content=SAID, metadata=_metadata()))
    got = json.loads(r.json()["text"])
    assert got["facts_unsupported"][0]["field_id"] == "p1.eligibility_basis"
    assert [f["field_id"] for f in got["facts"]] == ["p1.eligibility_basis"]

    client.app.state.rate_limiter._buckets.clear()
    with patch("app.services.anthropic_or_fallback.route_stream_with_fallback", _stream_of(text)):
        with client.stream("POST", "/v1/chat", headers=hdr, json=chat_request(
                system_prompt="", user_content=SAID, stream=True, metadata=_metadata())) as s:
            raw = s.read().decode()
    env = json.loads(next(b.split("\n")[1][6:] for b in raw.strip().split("\n\n")
                          if b.startswith("event: envelope")))
    assert json.loads(env["text"])["facts_unsupported"][0]["verdict"] == "insufficient"


def test_with_the_mode_off_the_route_is_untouched(client, free_user, monkeypatch):
    from unittest.mock import patch
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata
    ask = AsyncMock(return_value=_body(("insufficient", 0.99)))
    monkeypatch.setattr(tj, "ask", ask)
    text = json.dumps(_route_envelope(), ensure_ascii=False)

    async def fake(provider_router, request, db, settings):
        return ChatResponse(text=text, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                            provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", headers={**free_user["headers"], "X-App-ID": "n400"},
                        json=chat_request(system_prompt="", user_content=SAID, metadata=_metadata()))
    assert "facts_unsupported" not in json.loads(r.json()["text"])
    ask.assert_not_called()


# --- the client's per-field catalogue: folded gates and volunteered values -----------------------
#
# Fixtures are the three real mints in qa/labelled-option-mints.json that the
# agenda-only scope could not see (auditor, 2026-09-22): the agenda is what to
# ASK next, folded, so a node's gate and a value volunteered for a later node
# are on no line.

FULL_NAME_AGENDA = (
    "q_p1_full_name | Part 2: About you | p1.first_name,p2.middle_name,p1.last_name | "
    "Give me your name exactly as it is printed on your green card.\n"
    "q_p2_gender | Part 2: About you | p2.gender | Are you male or female? | options: male, female\n"
)
MARITAL_AGENDA = (
    "q_p5_marital_status | Part 5: Your marital history | p5.marital_status | "
    "What is your current marital status? | options: single, married, divorced, widowed\n"
)
CHILD_UNION_AGENDA = (
    "q_p6_child1 | Part 6: Your children | p6.child1.name,p6.child1.dob,p6.child1.residence,p6.child1.relationship | "
    "Tell me about your oldest child. | options: resides_with_me, not_with_me, biological, stepchild, adopted\n"
)
CATALOGUE = {
    "p2.has_middle_name": ["yes", "no"],
    "p2.gender": ["male", "female"],
    "p5.marital_status": ["single", "married", "divorced", "widowed"],
    "p5.spouse_citizen_how": ["by_birth", "other"],
    "p6.child1.residence": ["resides_with_me", "not_with_me"],
    "p6.child1.relationship": ["biological", "stepchild", "adopted"],
    "p1.eligibility_basis": ["general_provision", "spouse_usc", "vawa", "other"],
}
NAME_SAID = "Ana Lucia Torres, Ana Lucia is the first name, Torres is the last"
MARITAL_SAID = "I'm married. My husband is Daniel Cho, he's a U.S. citizen, and we got married in 2019."


def test_a_folded_gate_on_no_agenda_line_is_checked_against_the_standing_question():
    turn = json.loads(_turn(_fact("p2.has_middle_name", "no", "Ana Lucia is the first name, Torres is the last")))
    (c,) = es.enum_facts_to_check(turn, FULL_NAME_AGENDA, NAME_SAID, CATALOGUE)
    assert c["field_id"] == "p2.has_middle_name" and c["options"] == ["no", "yes"]
    assert c["question"] == "Give me your name exactly as it is printed on your green card."
    assert c["applicant_said"] == NAME_SAID


def test_a_value_volunteered_for_a_later_node_is_checked_and_one_outside_its_options_is_not():
    volunteered = _fact("p5.spouse_citizen_how", "by_birth", "he's a U.S. citizen")
    turn = json.loads(_turn(_fact("p5.marital_status", "married", "I'm married"), volunteered))
    got = es.enum_facts_to_check(turn, MARITAL_AGENDA, MARITAL_SAID, CATALOGUE)
    assert [c["field_id"] for c in got] == ["p5.spouse_citizen_how"]
    assert got[0]["options"] == ["by_birth", "other"]
    assert got[0]["question"].startswith("What is your current marital status?")
    # The real s1-v6-full#31 mint was `citizen`, which is no option at all:
    # that is the outside-options guard's case, never a question for Jev.
    turn = json.loads(_turn(_fact("p5.spouse_citizen_how", "citizen", "he's a U.S. citizen")))
    assert es.enum_facts_to_check(turn, MARITAL_AGENDA, MARITAL_SAID, CATALOGUE) == []


def test_without_the_catalogue_the_same_folded_mints_are_invisible():
    turn = json.loads(_turn(_fact("p2.has_middle_name", "no", "Ana Lucia is the first name")))
    assert es.enum_facts_to_check(turn, FULL_NAME_AGENDA, NAME_SAID) == []
    turn = json.loads(_turn(_fact("p5.spouse_citizen_how", "by_birth", "he's a U.S. citizen")))
    assert es.enum_facts_to_check(turn, MARITAL_AGENDA, MARITAL_SAID, None) == []


def test_the_catalogue_names_options_per_field_where_the_agenda_line_carries_a_union():
    turn = json.loads(_turn(_fact("p6.child1.residence", "resides_with_me", "she's my daughter, my own daughter")))
    said = "February eleven two thousand one, she's my daughter, my own daughter"
    (with_union,) = es.enum_facts_to_check(turn, CHILD_UNION_AGENDA, said)
    assert with_union["options"] == ["adopted", "biological", "not_with_me", "resides_with_me", "stepchild"]
    (per_field,) = es.enum_facts_to_check(turn, CHILD_UNION_AGENDA, said, CATALOGUE)
    assert per_field["options"] == ["not_with_me", "resides_with_me"]
    assert per_field["question"] == "Tell me about your oldest child."


def test_a_field_the_catalogue_does_not_list_is_not_a_choice_field():
    turn = json.loads(_turn(_fact("p1.a_number", "204881367", "A 204 881 367")))
    assert es.enum_facts_to_check(turn, AGENDA, "A 204 881 367", CATALOGUE) == []
    # ...and the literal-in-cited-words rule still carries a quoted yes.
    turn = json.loads(_turn(_fact("p2.has_middle_name", "no", "no middle name")))
    assert es.enum_facts_to_check(turn, FULL_NAME_AGENDA, "no middle name", CATALOGUE) == []


@pytest.mark.parametrize("bad", ["not a map", {"p2.has_middle_name": "yes,no"}, {"p2.has_middle_name": []},
                                 {"p2.has_middle_name": [1, 2]}, {3: ["yes", "no"]}])
def test_a_malformed_catalogue_falls_back_to_the_agenda_scope(bad):
    assert es.choice_catalogue(bad) == {}
    (c,) = es.enum_facts_to_check(json.loads(T008), AGENDA, SAID, bad)
    assert c["field_id"] == "p1.eligibility_basis" and c["options"] == ["general_provision", "other", "spouse_usc", "vawa"]
    turn = json.loads(_turn(_fact("p2.has_middle_name", "no", "Ana Lucia is the first name")))
    assert es.enum_facts_to_check(turn, FULL_NAME_AGENDA, NAME_SAID, bad) == []


def test_the_catalogue_reaches_the_check_from_request_metadata(client, free_user, monkeypatch):
    """A folded gate, an agenda that does not list it, and the catalogue in
    metadata: the marker must reach the wire. Without the wiring the check
    sees no options for the field and marks nothing."""
    from unittest.mock import patch
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata
    monkeypatch.setattr("app.services.document_generation._typesafe_mode", lambda: ("primary", "k"))
    ask = AsyncMock(return_value=_body(("insufficient", 0.83)))
    monkeypatch.setattr(tj, "ask", ask)
    env = _route_envelope()
    env["facts"] = [{"field_id": "p2.has_middle_name", "value": "no", "source": "applicant",
                     "provenance": {"utterance": "Ana Lucia is the first name, Torres is the last"}}]
    text = json.dumps(env, ensure_ascii=False)
    hdr = {**free_user["headers"], "X-App-ID": "n400"}

    async def fake(provider_router, request, db, settings):
        return ChatResponse(text=text, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                            provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})
    md = {**_metadata(), "agenda": FULL_NAME_AGENDA, "choice_fields": {"p2.has_middle_name": ["yes", "no"]}}
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", headers=hdr, json=chat_request(
            system_prompt="", user_content=NAME_SAID, metadata=md))
    got = json.loads(r.json()["text"])
    assert [u["field_id"] for u in got.get("facts_unsupported", [])] == ["p2.has_middle_name"]
    sent = ask.call_args.args[1]["facts"][0] if ask.call_args.args[1:] else ask.call_args.kwargs["state"]["facts"][0]
    assert sent["options"] == ["no", "yes"]
