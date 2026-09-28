"""An identifier read back must be the identifier filed (the auditor, 2026-09-28).

spectrum-minh-r4 t17: he said "627... 18... 4402", the lane filed p2.ssn =
627184402, and the reply said "9 2 7 1 8 4 4 0 2". The card was right and the
sentence was wrong, and he "corrected" a correct value. The filed value wins
(Scott's sentence-vs-card ruling). This is the one guard that rewrites SPOKEN
text, so it is tested on both paths that speak: the tail and the stream.
"""
import json
import logging

import pytest

from app.services.n400_interviewer_guard import (
    correct_identifier_readback, guard_response_text,
)
from app.services.n400_sentence_stream import ReleaseController

SSN = [{"field_id": "p2.ssn", "value": "627184402"}]


def _turn(reply: str, facts=SSN) -> str:
    # facts BEFORE reply, the order the prompt requires and the stream needs.
    return json.dumps({"schema_version": 1, "turn_id": "t17", "intent": {"type": "answer"},
                       "facts": facts, "deferred": [], "section_checkpoint": None,
                       "asking": None, "interview_over": False, "reply": {"en": reply}},
                      ensure_ascii=False)


# --- the rule -----------------------------------------------------------------

def test_minhs_misspoken_ssn_is_spoken_as_filed():
    out = json.loads(guard_response_text(
        _turn("9 2 7 1 8 4 4 0 2. When you're naturalized, do you want a new card?"), None, "t17"))
    assert out["reply"]["en"].startswith("6 2 7 1 8 4 4 0 2.")
    assert out["facts"] == SSN, "the filed value is the truth and is never touched"


@pytest.mark.parametrize("spoken", [
    "627-18-4402, got it.",                      # a correct read-back
    "You moved in on 2019-06-01 at 2100 East.",  # a date and a street number
    "Your A-Number is A 2 1 4 4 4 3 2 1 0.",     # an identifier NOT filed this turn
    "That's 1 2 3 4 5 6 7 8 9, a number of her own.",  # too far from the SSN to be a misspeak
])
def test_what_is_not_a_misspoken_readback_is_left_alone(spoken):
    assert correct_identifier_readback(spoken, SSN) == (spoken, [])


def test_the_spoken_layout_is_kept_and_a_phone_is_covered():
    facts = [{"field_id": "p11.mobile_phone", "value": "2145550177"}]
    fixed, changes = correct_identifier_readback("Your cell is (214) 555-0171, right?", facts)
    assert fixed == "Your cell is (214) 555-0177, right?"
    assert changes == [{"field_id": "p11.mobile_phone", "digits_differing": 1}]


def test_two_identifiers_of_one_length_each_go_to_the_nearest():
    facts = [{"field_id": "p2.ssn", "value": "627184402"},
             {"field_id": "p1.a_number", "value": "A214443210"}]
    fixed, _ = correct_identifier_readback(
        "SSN 9 2 7 1 8 4 4 0 2 and A-Number A 2 1 4 4 4 3 2 1 9.", facts)
    assert fixed == "SSN 6 2 7 1 8 4 4 0 2 and A-Number A 2 1 4 4 4 3 2 1 0."


def test_the_log_never_carries_the_digits(caplog):
    with caplog.at_level(logging.WARNING):
        guard_response_text(_turn("9 2 7 1 8 4 4 0 2. Next question?"), None, "t17")
    lines = [r.getMessage() for r in caplog.records if "identifier_readback" in r.getMessage()]
    assert lines, "the correction is logged"
    for line in lines:
        assert "627184402" not in line.replace(" ", "") and "927184402" not in line.replace(" ", "")


# --- the stream: what she hears is exactly the prefix of the final reply --------

def _stream(text: str) -> list[str]:
    rc = ReleaseController(locale="en", agenda=None, known_facts=None, turn_id="t17")
    released = []
    for ch in text:   # one character at a time: the worst-case delta split
        released += [e["text"] for e in rc.feed(ch)]
    return released


def test_the_stream_releases_the_corrected_sentence_not_the_misspoken_one():
    text = _turn("9 2 7 1 8 4 4 0 2. When you're naturalized, do you want a new card?")
    released = _stream(text)
    assert released == ["6 2 7 1 8 4 4 0 2."], released
    final = json.loads(guard_response_text(text, None, "t17"))["reply"]["en"]
    assert " ".join(final.split()).startswith(" ".join(released)), (
        "the tail and the stream must agree, or what she heard is not the reply")


# --- a probation she never had is confirmed empty, never "no" (jorge-r4 t45) ---

def test_confirmed_empty_is_not_an_off_option_value_for_probation_only():
    """"no" on p9.probation_completed prints as a probation she did NOT
    complete. The lane's stated-none shape ("") is the right answer when she
    never had one, so the options marker must not call it invalid there, and
    must still call "" invalid on an ordinary yes/no gate."""
    from app.services.n400_interviewer_guard import mark_values_outside_declared_options
    agenda = ("q_p9_crimes_probation_completed | Part 9: Your record | p9.probation_completed | "
              "If you received a suspended sentence, were placed on probation, or were paroled, "
              "have you completed it? | options: yes, no\n")
    ok = json.dumps({"facts": [{"field_id": "p9.probation_completed", "value": ""}]})
    assert mark_values_outside_declared_options(ok, agenda)[1] == []
    gate = agenda.replace("p9.probation_completed", "p9.has_trip3")
    bad = json.dumps({"facts": [{"field_id": "p9.has_trip3", "value": ""}]})
    assert [o["field_id"] for o in mark_values_outside_declared_options(bad, gate)[1]] == ["p9.has_trip3"]


# --- a stated trip count closes the list (rosa-r4 t46) ---------------------

from app.services.n400_interviewer_guard import close_trips_on_stated_count, drop_stale_asking

TWICE = "Hi, I'm her daughter, Lucía. She went to Guadalajara twice, I have the dates here."


def _trips(*fields):
    return json.dumps({"facts": [{"field_id": f, "value": "x", "provenance": {"utterance": "Guadalajara"}}
                                 for f in fields], "reply": {"es": "Listo."}})


def test_her_count_with_the_last_row_filed_mints_the_next_gate_no():
    out, info = close_trips_on_stated_count(_trips("p8.trip1.countries", "p8.trip2.countries"), TWICE)
    gate = [f for f in json.loads(out)["facts"] if f["field_id"] == "p8.has_trip3"]
    assert gate and gate[0]["value"] == "no" and gate[0]["provenance"]["utterance"] == "twice"
    assert info == {"field_id": "p8.has_trip3", "count": 2}


@pytest.mark.parametrize("facts,said", [
    (("p8.trip1.countries",), TWICE),                        # the counted rows are not all filed
    (("p8.trip1.countries", "p8.trip2.countries"), "She went to Guadalajara, I have the dates here."),  # no count
    (("p8.trip1.countries",), "Once I got back I started at Lotus Nails."),  # "once" is a conjunction
])
def test_no_count_of_hers_means_no_gate(facts, said):
    out, info = close_trips_on_stated_count(_trips(*facts), said)
    assert info is None and not any(f["field_id"].startswith("p8.has_trip") for f in json.loads(out)["facts"])


# --- the asking drop says whether a settled question was still spoken (priya-r6 t8)

AGENDA_JOBS = "q_p7_more_jobs1 | Part 7: Your work | p7.has_job2 | Anything before that? | options: yes, no\n"


@pytest.mark.parametrize("reply,spoken", [
    ("That covers five years. Did you work anywhere else before Arcwell?", True),
    ("That covers five years. Part 8 is your trips.", False),
])
def test_the_drop_marks_a_question_still_spoken_about_the_filled_node(reply, spoken):
    t = json.dumps({"facts": [{"field_id": "p7.has_job2", "value": "yes"}],
                    "asking": {"node_id": "q_p7_more_jobs1", "field_ids": ["p7.has_job2"]},
                    "reply": {"en": reply}})
    out, info = drop_stale_asking(t, AGENDA_JOBS)
    assert info["question_still_spoken"] is spoken
    assert json.loads(out)["asking_dropped"]["question_still_spoken"] is spoken
