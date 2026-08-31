"""How long is a chat turn silent before the first byte, and where does it go?

#822 flushed the SSE head with the first body chunk and killed 5.00s of a
10.34s wait. The remaining **5.32s is pre-flight**: document extraction, the
generation classifier, recall, prompt assembly. It matters because the two
turns lost on 2026-08-29 died at 3s and 9s, INSIDE that window, where no
heartbeat exists to keep the socket alive.

The obvious fix is to start the SSE envelope before that work and move the
pre-flight inside the generator. That is a restructure of the hottest path in
the product, and **nobody currently knows which phase owns the 5.32s.**
Extraction, the classifier and recall are all plausible and only one of them
is worth restructuring around.

So this is the INSTRUMENT, not the fix. Every other number that decided
something this week was measured first and several of them overturned the
plan: a 47% failure rate that priced out at $0.84, an "82.1% inferable" that
was one patch type, a CI slowdown that turned out to be runner variance.

What is pinned here is that the instrument cannot go quiet, because a
pre-flight probe that silently stops emitting looks exactly like a fast
pre-flight.
"""

from __future__ import annotations

import pytest

from tests.conftest import chat_request


def _h(user):
    return {**user["headers"], "X-App-ID": "shouldersurf", "X-App-Build": "1330"}


def _records(caplog):
    return [r for r in caplog.records if r.message == "chat_preflight"]


def test_every_turn_emits_a_preflight_line(client, free_user, caplog):
    """EVERY turn, not only slow ones.

    A threshold would make the fast turns invisible and the distribution
    unknowable, and "how long is pre-flight normally" is the question this
    exists to answer. It is also the same mistake as a detector that only
    logs when it fires: absence would stop meaning anything.
    """
    with caplog.at_level("INFO"):
        r = client.post("/v1/chat", json=chat_request(
            user_content="What did we decide?"), headers=_h(free_user))
    assert r.status_code == 200
    recs = _records(caplog)
    assert len(recs) == 1, f"expected one preflight line, got {len(recs)}"


def test_the_line_carries_a_total_and_a_breakdown(client, free_user, caplog):
    """A phase timing split across several log lines cannot be read as a
    total, and nobody reconstructs it. One line, or it is not a measurement.
    """
    with caplog.at_level("INFO"):
        client.post("/v1/chat", json=chat_request(user_content="hello"),
                    headers=_h(free_user))
    d = _records(caplog)[0].__dict__
    assert isinstance(d["total_ms"], (int, float))
    assert d["total_ms"] >= 0
    assert isinstance(d["marks_ms"], dict)
    # The two boundaries that make the number attributable rather than a
    # bare duration. Without them "pre-flight took 5s" names no phase.
    assert "before_documents" in d["marks_ms"]
    assert "preflight_end" in d["marks_ms"]
    assert d["marks_ms"]["preflight_end"] >= d["marks_ms"]["before_documents"]


def test_the_dimensions_needed_to_slice_it_are_present(
    client, free_user, caplog
):
    """The whole hypothesis is that DOCUMENTS own the window: the incident
    turn carried 400,653 bytes. A duration with no document count cannot
    confirm or refute that, so the slice fields ride on the same line."""
    with caplog.at_level("INFO"):
        client.post("/v1/chat", json=chat_request(user_content="hello"),
                    headers=_h(free_user))
    d = _records(caplog)[0].__dict__
    for field in ("doc_count", "call_type", "streaming", "has_turn_id"):
        assert field in d, f"cannot slice pre-flight by {field}"


def test_a_probe_failure_never_breaks_the_turn(client, free_user, monkeypatch,
                                               caplog):
    """An instrument must never break the thing it watches. Proven by making
    the emit itself raise, rather than by trusting the try/except to be
    there."""
    import app.routers.chat as chat_mod

    real = chat_mod.logger.info

    def _boom(msg, *a, **kw):
        if msg == "chat_preflight":
            raise RuntimeError("probe exploded")
        return real(msg, *a, **kw)

    monkeypatch.setattr(chat_mod.logger, "info", _boom)
    r = client.post("/v1/chat", json=chat_request(user_content="hello"),
                    headers=_h(free_user))
    assert r.status_code == 200, "a broken probe took the turn down with it"
