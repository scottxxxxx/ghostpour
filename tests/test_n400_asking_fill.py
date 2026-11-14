"""A null asking beside a spoken question is filled from the agenda's first
open line (the auditor, 2026-11-13, from Steven's case 515e9bb1 and the Haiku
A/B where 6 of 13 turns asked with asking null)."""
import json

from app.services.n400_interviewer_guard import (
    ASKING_FILLED_REASON, fill_null_asking, guard_response_text,
)

AGENDA = (
    "q_p1_full_name | Part 1: Your name | p1.first_name,p1.last_name | What is your full name?\n"
    "q_p1_dob | Part 1: Your name | p1.dob | What is your date of birth?"
)
NAME_FACT = {"field_id": "p1.first_name", "value": "Ana", "value_type": "string",
             "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": "Ana Diaz"}}


def _turn(**kw):
    t = {"schema_version": 1, "turn_id": "t_001", "intent": {"type": "answer"},
         "reply": {"en": "Thanks. What is your full name?"}, "facts": [], "asking": None}
    t.update(kw)
    return json.dumps(t)


def test_a_question_with_null_asking_is_filled_from_the_first_open_line():
    out, info = fill_null_asking(_turn(), AGENDA)
    t = json.loads(out)
    assert t["asking"] == {"node_id": "q_p1_full_name", "field_ids": ["p1.first_name", "p1.last_name"]}
    assert t["asking_filled"]["reason"] == ASKING_FILLED_REASON and info["node_id"] == "q_p1_full_name"


def test_a_line_this_response_filled_is_skipped():
    out, _ = fill_null_asking(_turn(facts=[NAME_FACT]), AGENDA)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_dob"


def test_no_question_means_no_fill():
    text = _turn(reply={"en": "Thanks, got it."})
    assert fill_null_asking(text, AGENDA) == (text, None)


def test_a_set_asking_is_never_touched():
    text = _turn(asking={"node_id": "q_p1_dob", "field_ids": ["p1.dob"]})
    assert fill_null_asking(text, AGENDA) == (text, None)


def test_a_checkpoint_keeps_its_null_asking():
    text = _turn(section_checkpoint={"part": 1, "awaiting_confirmation": True},
                 reply={"en": "That's Part 1. Is that right?"})
    assert fill_null_asking(text, AGENDA) == (text, None)


def test_prose_and_an_empty_agenda_pass_through():
    assert fill_null_asking("not json?", AGENDA) == ("not json?", None)
    assert fill_null_asking(_turn(), "") == (_turn(), None)


def test_the_chain_runs_it_and_logs_its_own_status(caplog):
    out = guard_response_text(_turn(), AGENDA, "t_001")
    assert json.loads(out)["asking"]["node_id"] == "q_p1_full_name"
    assert "n400_asking_filled turn_id=t_001 node_id=q_p1_full_name matched=spoken_question" in caplog.text


def test_the_spoken_question_wins_over_the_first_open_line():
    """The stream route fixture's shape: eligibility is first and open, but
    she heard the A-Number question."""
    out, info = fill_null_asking(_turn(reply={"en": "Noted. Now, what is your date of birth?"}), AGENDA)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_dob" and info["matched"] == "spoken_question"


def test_an_unmatched_question_falls_back_to_the_first_open_line():
    out, info = fill_null_asking(_turn(reply={"en": "Shall we keep going?"}), AGENDA)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_full_name" and info["matched"] == "first_open"
