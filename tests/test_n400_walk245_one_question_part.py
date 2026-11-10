"""Only a part of two or more questions is read back (Scott, 2026-11-10).

Case 245e6428, build 216: Part 13's preparer "no" was filed and the lane went
on with no checkpoint; then Part 12's interpreter "no" alone was read back
("That's Part 12: no interpreter used. Is that right?"), and that "?" ended
the app's before-we-begin with the documents question left without buttons.
Scott: "we do not have to do a section confirm for the question about using
an interpreter ... since it is a section with >= 2 questions, that should be
the determinator as to whether we ask for a section confirmation."

Turn shapes are the real ones from GP's live log (t_002, t_003).
"""
import json
from unittest.mock import patch

from app.services.n400_interviewer_guard import (
    CHECKPOINT_CONFIRM_REMINDER, ONE_QUESTION_PART_REMINDER,
    REFUSED_ONE_QUESTION_PART, REFUSED_RIDES_NEXT_QUESTION,
    checkpoint_is_refused, checkpoint_reminder, part_answer_count,
)

AGENDA = (
    "q_p12_interpreter_gate | Part 12: Your interpreter | p12.has_interpreter | Did you use anyone as an "
    "interpreter? | options: yes, no\n"
    "q_p1_eligibility_basis | Part 1: Your eligibility | p1.eligibility_basis | Which one applies to you? "
    "| options: general_provision, spouse_usc, vawa"
)
KNOWN = (
    "p13.has_preparer: no (options: yes, no)\n"
    "p4.current_address.city: Austin\n"
    "p4.current_address.state: TX\n"
)
NO_INTERPRETER = {"field_id": "p12.has_interpreter", "value": "no", "value_type": "string",
                  "provenance": {"source": "user_stated", "confidence": 0.95, "utterance": "No"}}
CP12 = {"part": 12, "section": "Part 12: Your interpreter", "awaiting_confirmation": True}
ASK_P1 = {"node_id": "q_p1_eligibility_basis", "field_ids": ["p1.eligibility_basis"]}


def _turn(**kw):
    base = {"schema_version": 1, "turn_id": "t_003", "intent": {"type": "answer"},
            "facts": [], "deferred": [], "clarification": None, "conflict": None,
            "escalation": None, "complete": True, "asking": None,
            "section_checkpoint": None, "interview_over": False, "reply": {"en": "ok"}}
    base.update(kw)
    return json.dumps(base)


# The real t_003.
READBACK_12 = _turn(facts=[NO_INTERPRETER], section_checkpoint=CP12,
                    reply={"en": "That's Part 12: no interpreter used. Is that right?"})


def test_the_walk_245_interpreter_read_back_is_refused():
    info = checkpoint_is_refused(READBACK_12, AGENDA, KNOWN, "No")
    assert info["code"] == REFUSED_ONE_QUESTION_PART and info["part"] == 12
    assert checkpoint_reminder(info) == ONE_QUESTION_PART_REMINDER


def test_the_right_shape_files_and_asks_the_next_part():
    ok = _turn(facts=[NO_INTERPRETER], asking=ASK_P1,
               reply={"en": "Which of the Reason for Filing boxes applies to you?"})
    assert checkpoint_is_refused(ok, AGENDA, KNOWN, "No") is None


def test_a_one_question_ride_along_is_sent_on_not_back_to_confirm():
    # The September shape on a one-question part: the ride-along refusal would
    # ask for "Is that right?", which is exactly what Scott ruled out here.
    text = _turn(facts=[NO_INTERPRETER], asking=ASK_P1,
                 section_checkpoint={**CP12, "awaiting_confirmation": False},
                 reply={"en": "No interpreter. Which of the Reason for Filing boxes applies to you?"})
    info = checkpoint_is_refused(text, AGENDA, KNOWN, "No")
    assert info["code"] == REFUSED_ONE_QUESTION_PART
    assert checkpoint_reminder(info) != CHECKPOINT_CONFIRM_REMINDER


def test_an_answer_filed_last_turn_still_counts_as_one():
    known = KNOWN + "p12.has_interpreter: no (options: yes, no)\n"
    text = _turn(section_checkpoint=CP12, reply={"en": "That's Part 12: no interpreter used. Is that right?"})
    assert checkpoint_is_refused(text, "", known, "No")["code"] == REFUSED_ONE_QUESTION_PART


def test_an_interpreter_yes_part_of_two_questions_is_confirmed():
    known = KNOWN + "p12.has_interpreter: yes (options: yes, no)\n"
    text = _turn(facts=[{"field_id": "p12.interpreter_language", "value": "Spanish"}],
                 section_checkpoint=CP12,
                 reply={"en": "That's Part 12: your interpreter reads Spanish. Is that right?"})
    assert part_answer_count(json.loads(text), 12, known) == 2
    assert checkpoint_is_refused(text, "", known, "Spanish") is None


def test_a_two_question_ride_along_is_still_sent_back_to_confirm():
    known = KNOWN + "p12.has_interpreter: yes (options: yes, no)\n"
    text = _turn(facts=[{"field_id": "p12.interpreter_language", "value": "Spanish"}], asking=ASK_P1,
                 section_checkpoint={**CP12, "awaiting_confirmation": False},
                 reply={"en": "Spanish. Which of the Reason for Filing boxes applies to you?"})
    p1_only = AGENDA.split("\n")[1]
    assert checkpoint_is_refused(text, p1_only, known, "Spanish")["code"] == REFUSED_RIDES_NEXT_QUESTION


def test_a_capture_gap_is_not_an_answer():
    # She answered, the lane could not record it: that is not a second question.
    gap = {"field_id": "p12.interpreter_language", "origin": "capture_gap", "reason": "not recorded"}
    count = part_answer_count({"facts": [NO_INTERPRETER], "deferred": [gap]}, 12, KNOWN)
    assert count == 1


# --- through the real route ------------------------------------------------------

def test_the_route_retries_a_one_question_read_back_into_the_next_question(client, free_user):
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata
    good = _turn(facts=[NO_INTERPRETER], asking=ASK_P1,
                 reply={"en": "Which of the Reason for Filing boxes applies to you?"})
    replies, sent = [READBACK_12, good], []

    async def fake(provider_router, request, db, settings):
        sent.append(request.user_content)
        return ChatResponse(text=replies[min(len(sent), 2) - 1], input_tokens=100, output_tokens=50,
                            model="claude-sonnet-5", provider="anthropic",
                            usage={"input_tokens": 100, "output_tokens": 50})
    client.app.state.rate_limiter._buckets.clear()
    body = chat_request(system_prompt="", user_content="No",
                        metadata=_metadata(known_facts=KNOWN, agenda=AGENDA, turn_id="t_003"))
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", json=body, headers={**free_user["headers"], "X-App-ID": "n400"})
    assert r.status_code == 200 and len(sent) == 2
    assert sent[1].endswith(ONE_QUESTION_PART_REMINDER)
    served = json.loads(r.json()["text"])
    assert served["section_checkpoint"] is None
    assert served["asking"]["node_id"] == "q_p1_eligibility_basis"
    assert served["checkpoint_refused"]["code"] == REFUSED_ONE_QUESTION_PART


# --- the sentence stream, which the I-765 lane shares ------------------------------

def _stream(call_type):
    from app.services.n400_sentence_stream import ReleaseController
    rc = ReleaseController(locale="en", agenda=AGENDA, known_facts=KNOWN, turn_id="t_003",
                           call_type=call_type)
    released = [e.get("text") for e in rc.feed(READBACK_12) if isinstance(e, dict)]
    return released, rc


def test_the_n400_stream_holds_a_one_question_read_back():
    released, rc = _stream("n400_interviewer_turn")
    assert rc.buffered_reason == "checkpoint_refused"
    assert not any("Is that right" in (t or "") for t in released), "she never hears it"


def test_the_ruling_is_n400s_and_the_i765_stream_is_untouched():
    _, rc = _stream("i765_interviewer_turn")
    assert rc.buffered_reason != "checkpoint_refused"
