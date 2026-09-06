"""A part is not complete while its own node is still on the agenda.

conf-v25 English turn 79: she answered ONE of six oath items. The lane
minted p9.willing_bear_arms only and said "that's a yes across the board on
the oath questions. That completes Part 9". Five required oath fields did
not exist until turn 94. It read back five facts it had never recorded for
fifteen turns and summarised Part 9 as confirmed twice; she said yes both
times. Had the interview ended anywhere in there, five required fields
would have printed BLANK after she was twice told they were recorded.

Everything else in that run came from it: the final read-back started at 80
with a node still open, the node closed at 94, and at 96 the lane started
the whole read-back again from Part 1 and never terminated.

GP already held the contradiction. The agenda is the record of what is
unanswered and q_p9_oath stayed on it from 78 to 94.
"""
import json

import pytest

from app.services.n400_interviewer_guard import (
    agenda_parts, checkpoint_contradicts_agenda,
    clear_interview_over_while_agenda_open, drop_contradicted_checkpoint,
    guard_response_text,
)

AGENDA = (
    "q_p9_oath | Part 9: The Oath | p9.support_constitution,p9.willing_bear_arms,"
    "p9.willing_noncombatant | Are you willing to take the full Oath?\n"
    "q_p12_interp | Part 12: Interpreter | p12.used | Did you use an interpreter?"
)


def _resp(**kw):
    base = {"intent": "answer", "facts": [], "reply": {"en": "ok"}}
    base.update(kw)
    return json.dumps(base)


def test_agenda_parts_reads_the_part_number_from_the_line():
    assert agenda_parts(AGENDA) == {"q_p9_oath": 9, "q_p12_interp": 12}


def test_the_turn_79_shape_is_caught():
    text = _resp(section_checkpoint={"part": 9, "section": "The Oath",
                                     "awaiting_confirmation": True},
                 reply={"en": "That's a yes across the board. That completes Part 9."})
    info = checkpoint_contradicts_agenda(text, AGENDA)
    assert info is not None
    assert info["part"] == 9 and info["open_nodes"] == ["q_p9_oath"]


def test_a_part_with_no_open_node_is_fine():
    """The guard must not block a legitimate checkpoint, which is most of
    them. Part 5 has nothing on this agenda."""
    text = _resp(section_checkpoint={"part": 5, "section": "Marital history"})
    assert checkpoint_contradicts_agenda(text, AGENDA) is None


def test_no_checkpoint_and_no_agenda_are_both_safe():
    assert checkpoint_contradicts_agenda(_resp(), AGENDA) is None
    assert checkpoint_contradicts_agenda(
        _resp(section_checkpoint={"part": 9}), None) is None
    assert checkpoint_contradicts_agenda("not json", AGENDA) is None
    assert checkpoint_contradicts_agenda(
        _resp(section_checkpoint="a bare string"), AGENDA) is None


def test_dropping_removes_the_claim_when_a_retry_did_not_help():
    text = _resp(section_checkpoint={"part": 9, "section": "The Oath"})
    out, info = drop_contradicted_checkpoint(text, AGENDA)
    turn = json.loads(out)
    assert turn["section_checkpoint"] is None
    assert turn["checkpoint_dropped"]["part"] == 9
    assert info is not None


def test_interview_over_cannot_be_set_while_anything_is_open():
    """The failure this prevents is the one that reaches her form: an
    interview closed with required fields empty."""
    out, info = clear_interview_over_while_agenda_open(
        _resp(interview_over=True), AGENDA)
    turn = json.loads(out)
    assert turn["interview_over"] is False
    assert turn["interview_over_cleared"]["open_nodes"] == ["q_p12_interp", "q_p9_oath"]
    assert info is not None


def test_interview_over_survives_an_empty_agenda():
    out, info = clear_interview_over_while_agenda_open(_resp(interview_over=True), "")
    assert json.loads(out)["interview_over"] is True and info is None


def test_the_orchestrator_actually_clears_interview_over():
    """A guard nobody calls is decoration."""
    out = guard_response_text(_resp(interview_over=True), AGENDA, "t-105", "yes")
    assert json.loads(out)["interview_over"] is False
