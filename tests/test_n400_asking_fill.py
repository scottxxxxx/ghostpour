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


PARAPHRASE_AGENDA = (
    "q_p1_full_name | Part 1: Your name | p1.first_name,p1.last_name | What is your full name?\n"
    "q_p1_phone | Part 1: Contact | p1.phone | What is your daytime phone number?\n"
    "q_p1_a_number | Part 1: Your A-Number | p1.a_number | What is your USCIS number, also called your A-Number?\n"
    "q_p1_dob | Part 1: Your birth | p1.dob | What is your date of birth?"
)


def test_a_paraphrase_matches_a_later_line_not_the_first_open_one():
    """The auditor's case: reworded, and "number" alone must not pull the phone line."""
    out, info = fill_null_asking(_turn(reply={"en": "Got it. What's your A-Number?"}), PARAPHRASE_AGENDA)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_a_number" and info["matched"] == "spoken_question"


def test_a_shortened_question_matches_its_line():
    out, info = fill_null_asking(_turn(reply={"en": "Thanks. And when were you born, your birth date?"}),
                                 PARAPHRASE_AGENDA)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_dob" and info["matched"] == "spoken_question"


def test_a_spanish_paraphrase_matches():
    agenda = ("q_p1_full_name | Parte 1 | p1.first_name | ¿Cuál es su nombre completo?\n"
              "q_p1_dob | Parte 1 | p1.dob | ¿Cuál es su fecha de nacimiento?")
    out, info = fill_null_asking(_turn(reply={"es": "Gracias. ¿Y su fecha de nacimiento?"}), agenda)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_dob" and info["matched"] == "spoken_question"


def test_a_rare_shared_word_beats_a_common_one():
    """"number" sits on two lines, "marriages" on one; each line shares one
    word, so only rarity keeps the earlier phone line from winning the tie."""
    agenda = ("q_p1_full_name | Part 1 | p1.first_name | What is your full name?\n"
              "q_p1_phone | Part 1 | p1.phone | What is your daytime phone number?\n"
              "q_p1_ssn | Part 1 | p1.ssn | What is your Social Security number?\n"
              "q_p5_times_married | Part 5 | p5.times_married | How many marriages have you had?")
    out, info = fill_null_asking(_turn(reply={"en": "Okay. And the number of marriages?"}), agenda)
    assert json.loads(out)["asking"]["node_id"] == "q_p5_times_married" and info["matched"] == "spoken_question"


def test_one_shared_word_in_a_long_question_is_no_match():
    """She heard about children; the phone line shares only "number"."""
    out, info = fill_null_asking(_turn(reply={"en": "How many children do you have, by number?"}),
                                 PARAPHRASE_AGENDA)
    assert json.loads(out)["asking"]["node_id"] == "q_p1_full_name" and info["matched"] == "first_open"
