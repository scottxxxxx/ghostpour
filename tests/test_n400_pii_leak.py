"""Identifiers that reached GhostPour unmasked on the N-400 interviewer lane.

The vectors C1 to C13 are the auditor's Task 9 (N400 App
qa/N400-AGENT-TASKS-2026-09-24.md) VERBATIM: the client's SpokenDigits is
graded on the same thirteen, so if either reader drifts from them the two
disagree about what a dictated number is, and a leak one side cannot see
opens up. Do not bend a vector to make a test pass; a red one is a finding.
"""

import json
import logging
import re

import pytest

from app.services import n400_pii_leak as leak
from app.services.spoken_numbers import written

TASK9 = [
    ("C1", "seis dos siete, cuarenta y cuatro, noventa dieciocho", "627449018"),
    ("C2", "six two seven, forty four, ninety eighteen", "627449018"),
    ("C3", "seis dois sete, quarenta e quatro, noventa dezoito", "627449018"),
    ("C4", "seis dos siete cuarenta cuatro noventa dieciocho", "627449018"),
    ("C5", "tengo cuarenta y cuatro años", "tengo cuarenta y cuatro años"),
    ("C6", "I have forty four cows", "I have forty four cows"),
    ("C7", "I moved in twenty twenty", "I moved in 2020"),
    ("C8", "treinta y uno de enero", "treinta y uno de enero"),
    ("C9", "five five five, twelve thirty four", "5551234"),
    ("C10", "nací en mil novecientos noventa", "nací en mil novecientos noventa"),
    ("C11", "forty-four ninety eighteen", "449018"),
    ("C12", "veintidós treinta y tres once", "223311"),
    ("C13", "uno dos y tres", "uno dos y tres"),
]


@pytest.mark.parametrize("vid,said,want", TASK9, ids=[v[0] for v in TASK9])
def test_the_reader_matches_the_clients_vectors(vid, said, want):
    assert written(said) == want


# --- what counts as a leak ------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("627449018", "nine_digits"),                                   # R3's input, bare
    ("A627449018", "nine_digits"),                                  # an A-Number with its letter
    ("627-44-9018", "nine_digits"),
    ("seis dos siete, cuarenta y cuatro, noventa dieciocho", "nine_digits"),   # the auditor's run, case 1
    ("six two seven, forty four, ninety eighteen", "nine_digits"),             # case 3, English
    ("512-555-0100", "ten_digits"),
    ("(512) 555-0100", "ten_digits"),
    ("nancy.smith@example.com", "email"),
])
def test_an_unmasked_identifier_is_counted(text, kind):
    assert leak.shapes(text) == {kind: 1}


@pytest.mark.parametrize("text", [
    "[[SSN_1]]",
    "my number is [[SSN_1]] and my phone is [[PHONE_2]], email [[EMAIL_1]]",
    "born 1990-01-01",                      # dates stay unmasked in stage one; 8 digits
    "p2.dob: 1990-01-01\np4.zip: 78701",
    "1990-01-01, 2020-02-02",
    "I have forty four cows",
    "5551234",                              # seven digits, a local number, not an identifier shape
    "build 1970, version 1.18",
    "",
])
def test_a_masked_or_ordinary_turn_counts_nothing(text):
    assert leak.shapes(text) == {}


def test_every_source_the_contract_masks_is_checked():
    rows = leak.report(leak.INTERVIEWER_CALL_TYPE, {
        "user_content": "627449018",
        "conversation": "Interviewer: your phone?\nApplicant: 512-555-0100",
        "known_facts": "p10.email = nancy.smith@example.com",
    }, "t1")
    assert rows == [{"source": "user_content", "kind": "nine_digits", "count": 1},
                    {"source": "conversation", "kind": "ten_digits", "count": 1},
                    {"source": "known_facts", "kind": "email", "count": 1}]


def test_the_log_carries_shapes_never_the_digits(caplog):
    """A detector that wrote down what it found would be the leak."""
    with caplog.at_level(logging.WARNING, logger="ghostpour.n400_pii_leak"):
        leak.report(leak.INTERVIEWER_CALL_TYPE,
                    {"user_content": "my social is 627 44 9018, email nancy.smith@example.com"}, "t1")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "kind=nine_digits" in text and "kind=email" in text
    # The identifier's own digit groups, in any spacing, never appear. (A bare
    # "no three digits" check was tried first and matched the 400 in the
    # message's own name, n400_pii_unmasked.)
    assert not re.search(r"627|9018|6\D?2\D?7\D?4\D?4", text), text
    assert "nancy" not in text and "example" not in text


def test_another_lane_is_never_read(caplog):
    with caplog.at_level(logging.WARNING, logger="ghostpour.n400_pii_leak"):
        assert leak.report("n400_interview_turn", {"user_content": "627449018"}) == []
        assert leak.report(None, {"user_content": "627449018"}) == []
    assert not caplog.records


def test_a_bug_inside_the_counter_cannot_fail_the_turn(monkeypatch):
    def boom(_):
        raise RuntimeError("x")
    monkeypatch.setattr(leak, "shapes", boom)
    assert leak.report(leak.INTERVIEWER_CALL_TYPE, {"user_content": "627449018"}) == []


# --- the route --------------------------------------------------------------------

def _post_turn(client, free_user, user_content):
    from unittest.mock import patch
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata
    env = {"schema_version": 1, "turn_id": "t-leak-1", "facts": [], "deferred": [],
           "reply": {"en": "Thank you."}}

    async def fake(provider_router, request, db, settings):
        return ChatResponse(text=json.dumps(env), input_tokens=100, output_tokens=50,
                            model="claude-sonnet-5", provider="anthropic",
                            usage={"input_tokens": 100, "output_tokens": 50})
    hdr = {**free_user["headers"], "X-App-ID": "n400"}
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        return client.post("/v1/chat", headers=hdr, json=chat_request(
            system_prompt="", user_content=user_content, metadata=_metadata()))


def test_the_route_counts_an_unmasked_turn_and_not_a_masked_one(client, free_user, caplog):
    with caplog.at_level(logging.WARNING, logger="ghostpour.n400_pii_leak"):
        r = _post_turn(client, free_user, "seis dos siete, cuarenta y cuatro, noventa dieciocho")
        assert r.status_code == 200, r.text
        hits = [m.getMessage() for m in caplog.records]
        assert any("source=user_content kind=nine_digits count=1" in h for h in hits), hits
        caplog.clear()
        r = _post_turn(client, free_user, "[[SSN_1]]")
        assert r.status_code == 200, r.text
    assert not [m for m in caplog.records if "n400_pii_unmasked" in m.getMessage()]
