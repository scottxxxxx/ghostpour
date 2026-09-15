"""A checkpoint card never rides on the turn that moves on to the next question.

auditor offpath2 (2026-09-15), calls 5 and 6. Call 5 was a proper Part 1
checkpoint: asking null, section_checkpoint {part 1, awaiting_confirmation},
"Is that all correct?". She said "yes, that's right". Call 6, the
acknowledgement, came back with the SAME section_checkpoint and asking on
q_p1_full_name: "Great, Part 1 is confirmed. Now, I need your full legal name".
The client drew a second Part 1 card with Confirm and Fix over the name
question. Walk 1 call 44 and conf-v20 turns 6, 7, 12, 13, 56, 57 and 77 show
the same shape.

A checkpoint proper carries asking null, so GP drops a checkpoint beside a
named next question and marks it `asking_set`. The client has the same
backstop; this is GP's floor.
"""
import json

from app.services.n400_interviewer_guard import (
    REFUSED_ASKING_SET, drop_checkpoint_when_asking_set, guard_response_text,
)

CP = {"part": 1, "section": "Part 1: Eligibility", "awaiting_confirmation": True}


def _resp(**kw):
    base = {"schema_version": 1, "turn_id": "t_006", "intent": {"type": "answer"},
            "facts": [], "deferred": [], "reply": {"en": "ok"}, "asking": None,
            "section_checkpoint": None}
    base.update(kw)
    return json.dumps(base)


def test_the_offpath2_call_6_shape_is_dropped_and_marked():
    text = _resp(section_checkpoint=CP,
                 asking={"node_id": "q_p1_full_name", "field_ids": ["p1.full_name"]},
                 reply={"en": "Great, Part 1 is confirmed. Now, I need your full legal name."})
    out, info = drop_checkpoint_when_asking_set(text)
    turn = json.loads(out)
    assert turn["section_checkpoint"] is None
    assert turn["checkpoint_dropped"]["code"] == REFUSED_ASKING_SET == "asking_set"
    assert turn["checkpoint_dropped"]["part"] == 1
    assert turn["checkpoint_dropped"]["asking"] == "q_p1_full_name"
    # Only the card is wrong: the sentence that moves on, and the next
    # question, are left exactly as the lane sent them.
    assert turn["reply"] == {"en": "Great, Part 1 is confirmed. Now, I need your full legal name."}
    assert turn["asking"]["node_id"] == "q_p1_full_name"
    assert info is not None


def test_a_proper_checkpoint_with_asking_null_is_kept():
    """The call 5 shape: the card that asks for confirmation must survive."""
    text = _resp(section_checkpoint=CP, asking=None,
                 reply={"en": "Here's Part 1: on your own, five years. Is that all correct?"})
    out, info = drop_checkpoint_when_asking_set(text)
    assert json.loads(out)["section_checkpoint"] == CP
    assert info is None and out == text


def test_an_empty_asking_is_not_a_next_question():
    for asking in ({}, {"node_id": None, "field_ids": []}, "", "   "):
        text = _resp(section_checkpoint=CP, asking=asking)
        out, info = drop_checkpoint_when_asking_set(text)
        assert info is None, asking
        assert json.loads(out)["section_checkpoint"] == CP, asking


def test_a_bare_string_asking_counts_as_set():
    """The lane has sent asking as a bare node id before (s1-v6-full turn 38)."""
    text = _resp(section_checkpoint=CP, asking="q_p1_full_name")
    out, info = drop_checkpoint_when_asking_set(text)
    assert json.loads(out)["section_checkpoint"] is None
    assert info["asking"] == "q_p1_full_name"


def test_no_checkpoint_means_nothing_to_drop():
    text = _resp(asking={"node_id": "q_p1_full_name", "field_ids": ["p1.full_name"]})
    out, info = drop_checkpoint_when_asking_set(text)
    assert info is None and out == text
    assert drop_checkpoint_when_asking_set("not json") == ("not json", None)


def test_the_orchestrator_applies_it():
    """A guard nobody calls is decoration."""
    text = _resp(section_checkpoint=CP,
                 asking={"node_id": "q_p1_full_name", "field_ids": ["p1.full_name"]},
                 reply={"en": "Great, Part 1 is confirmed. What is your full legal name?"})
    out = json.loads(guard_response_text(text, "", "t_006", "yes, that's right"))
    assert out["section_checkpoint"] is None
    assert out["checkpoint_dropped"]["code"] == "asking_set"
