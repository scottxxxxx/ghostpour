"""The live log has to show CQ payloads whole.

A person detail measured 67,386 bytes on the wire against a 10,000 char
capture cap, so the surface most likely to carry a passthrough bug was
the one nobody could see whole, and it was stored as a truncated STRING
because a clipped JSON body no longer parses. Raised for the CQ proxy
routes only, with the buffer's real memory bound made explicit rather
than left implied by an entry count.
"""

import json

import pytest

from app.middleware import request_logging as rl


@pytest.fixture(autouse=True)
def _clean_buffer():
    rl._LOG_BUFFER.clear()
    rl._BUFFER_BYTES = 0
    yield
    rl._LOG_BUFFER.clear()
    rl._BUFFER_BYTES = 0


def test_cq_routes_get_the_large_cap_and_nothing_else_does():
    assert rl._body_cap("/v1/people/u-1/ent-9") == rl._MAX_BODY_LOG_LARGE
    assert rl._body_cap("/v1/quilt/u-1") == rl._MAX_BODY_LOG_LARGE
    assert rl._body_cap("/v1/chat") == rl._MAX_BODY_LOG
    assert rl._body_cap("/v1/reports/m-1") == rl._MAX_BODY_LOG
    # The cap is the reason ordinary traffic cannot flood the buffer, so
    # a prefix that swallowed everything would be the bug this reintroduces.
    assert rl._body_cap("/") == rl._MAX_BODY_LOG


def test_a_person_sized_payload_survives_whole_and_still_parses():
    """67KB is the measured real size. Under the old cap this came back
    as a truncated string; the point of the change is that it comes back
    as a dict you can actually read keys off."""
    payload = {"entity_id": "ent-9",
               "insights": [{"lens": "what_moves_them",
                             "evidence": [{"text": "x" * 60_000}]}]}
    raw = json.dumps(payload).encode()
    assert len(raw) > 60_000

    clipped = rl._clip(raw, rl._body_cap("/v1/people/u-1/ent-9"))
    parsed = rl._format_body_parsed(clipped, rl._MAX_BODY_LOG_LARGE)

    assert isinstance(parsed, dict)
    assert parsed == payload


def test_the_same_payload_on_an_ordinary_route_is_still_capped():
    raw = json.dumps({"blob": "x" * 60_000}).encode()
    clipped = rl._clip(raw, rl._body_cap("/v1/chat"))
    assert len(clipped) < 11_000


def test_truncation_says_so_instead_of_looking_whole():
    """Silent truncation reads as a complete payload, which is how a
    missing key gets blamed on the wrong side of a wire."""
    clipped = rl._clip(b"y" * 50_000, 100)
    assert clipped.startswith("y" * 100)
    assert "truncated at 100 chars" in clipped
    assert "50000 total" in clipped

    # Under the cap, untouched: no marker on a body that is complete.
    assert rl._clip(b"short", 100) == "short"
    assert rl._clip(b"", 100) is None
    assert rl._clip(None, 100) is None


def test_the_buffer_evicts_on_bytes_not_just_on_count(monkeypatch):
    """Raising the per-entry cap 13x means '1000 entries' stopped being a
    memory bound. Without this the fix trades a blind spot for a leak."""
    monkeypatch.setattr(rl, "_MAX_BUFFER_BYTES", 10_000)

    for i in range(20):
        rl._buffer_append({"request_id": f"r-{i}"}, size=1_000)

    assert rl._BUFFER_BYTES <= 10_000
    kept = [e["request_id"] for e in rl._LOG_BUFFER]
    assert "r-19" in kept          # newest survives
    assert "r-0" not in kept       # oldest evicted
    assert rl._BUFFER_BYTES == sum(e[rl._SIZE_KEY] for e in rl._LOG_BUFFER)


def test_the_entry_count_bound_still_holds(monkeypatch):
    monkeypatch.setattr(rl, "_BUFFER_ENTRIES", 5)
    for i in range(12):
        rl._buffer_append({"request_id": f"r-{i}"}, size=0)
    assert len(rl._LOG_BUFFER) == 5
    assert [e["request_id"] for e in rl._LOG_BUFFER][-1] == "r-11"


def test_the_size_bookkeeping_key_never_reaches_a_reader():
    """It is an implementation detail of eviction, not part of the log
    entry contract the dashboard renders."""
    rl._buffer_append({"request_id": "r-1", "path": "/v1/quilt/u"}, size=42)

    entries = rl.get_recent_logs()
    assert entries[0]["request_id"] == "r-1"
    assert rl._SIZE_KEY not in entries[0]

    one = rl.get_log_by_request_id("r-1")
    assert one is not None
    assert rl._SIZE_KEY not in one
    # Still tracked internally.
    assert rl._LOG_BUFFER[0][rl._SIZE_KEY] == 42
