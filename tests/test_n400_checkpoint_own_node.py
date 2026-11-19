"""A read-back re-asked with `asking` on the part's own checkpoint node is not
the next part's question (Scott's 1.0.2 run, 2026-11-19, t_011)."""
import json

from app.services.n400_interviewer_guard import (
    checkpoint_rides_next_question,
    drop_checkpoint_when_asking_set,
)


def _turn(node):
    return json.dumps({
        "schema_version": 1, "turn_id": "t_011", "intent": {"type": "off_topic"},
        "facts": [], "deferred": [], "clarification": None, "conflict": None,
        "escalation": None, "complete": False,
        "asking": {"node_id": node, "field_ids": []},
        "section_checkpoint": {"part": 2, "section": "Part 2: About you",
                               "awaiting_confirmation": True},
        "interview_over": False,
        "reply": {"en": "I'm not able to do that, I'm just here to help with your "
                        "form. So, is Part 2 correct as I read it back?"},
    })


def test_own_checkpoint_node_is_not_riding_the_next_question():
    assert checkpoint_rides_next_question(_turn("q_p2_checkpoint")) is None


def test_own_checkpoint_node_keeps_the_card():
    text = _turn("q_p2_checkpoint")
    out, info = drop_checkpoint_when_asking_set(text)
    assert info is None and out == text


def test_next_part_question_is_still_refused():
    assert checkpoint_rides_next_question(_turn("q_p3_address"))["part"] == 2
    assert drop_checkpoint_when_asking_set(_turn("q_p3_address"))[1] is not None


def test_another_parts_checkpoint_node_is_still_refused():
    assert checkpoint_rides_next_question(_turn("q_p3_checkpoint")) is not None
