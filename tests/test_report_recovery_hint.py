"""Asking for the transcript instead of always being sent it.

We keep a copy of every meeting transcript for 30 days. Measured
2026-08-15, that is not earning its keep: all 72 reports we have ever
built were built within SEVEN HOURS of capture (median effectively
zero, max 0.3 days), and 61% of stored transcripts never produced a
report at all. So we hold full meeting content for a month, for 16
different users, to insure a regeneration path nobody has exercised.

The durable copy is on the device, and the re-send mechanism is proven:
ShoulderSurf's entity-repair replay pushed 59 transcripts back through
POST /capture-transcript with X-CZ-Recovery in a single afternoon.

So the client should send NOTHING on a normal regenerate, and we should
ask when we actually need it. These fields are how we ask. They are
additive and dark: a client ignoring them behaves exactly as before,
which is what keeps this off the synchronized-deploy list.
"""

from __future__ import annotations

import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "app/routers/reports.py").read_text()


def test_the_existing_contract_is_untouched() -> None:
    """Changing the code or the status would break clients that branch
    on it today, which is the opposite of an additive change."""
    assert '"code": "no_meeting_data"' in SRC
    assert "status_code=404" in SRC


def test_we_tell_the_client_this_is_recoverable() -> None:
    assert '"recoverable": True' in SRC
    assert '"recovery_action": "capture_transcript"' in SRC


def test_we_reuse_the_existing_recovery_source_name() -> None:
    """SS shipped this exact case in May 2026 as report-404-replay, and
    it has fired twice in production (2026-06-16, 2026-07-14). Inventing
    a second name for the same event would split it across two labels in
    the logs, which is precisely what the header exists to prevent."""
    assert '"recovery_source": "report-404-replay"' in SRC
    assert "report-regenerate" not in SRC


def test_the_404_is_documented_as_latency_sensitive() -> None:
    """The client answers this 404 by uploading a whole transcript, so
    our lookup time comes out of their upload budget."""
    i = SRC.index('if not meeting_data["transcript"]')
    assert "Keep this 404 fast" in SRC[i - 600:i]


def test_recoverable_is_a_claim_about_our_side_only() -> None:
    """Whether the phone still holds the transcript is the client's to
    know. We are only saying we can finish if it arrives, and the
    comment has to keep saying so or someone will read the flag as a
    promise the data exists somewhere."""
    i = SRC.index('"recoverable": True')
    context = SRC[i - 900:i]
    assert "OUR side only" in context
    assert "client's to know" in context
