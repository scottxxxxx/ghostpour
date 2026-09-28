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
