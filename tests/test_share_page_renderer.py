"""The hosted share page renders SS's .shouldersurf bundle, traps included.

Fixtures are SS's synthetic bundles (AudioRoutingPrototype/Scripts/
make-share-fixture.py @60b3b89): every byte of content invented, the
SHAPE taken from a real MeetingRecord read off a device, and pinned on
their side against the real Swift BundleManifest decoder. They exercise
the three traps on purpose: dates as seconds since 2001-01-01, the
base64 double-encoded report with actions[] inside it, and mixed key
casing in one payload. `typical` (report, no transcript) is the common
share; `minimal` is the floor; `unknown-entry` must be ignored, not
treated as damage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.share_bundle import apple_date, decode_report, read_bundle, render_share_page

FIX = Path(__file__).parent / "fixtures" / "share"


def _b(name: str) -> bytes:
    return (FIX / f"fixture-{name}.shouldersurf").read_bytes()


def test_dates_are_seconds_since_2001_not_epoch():
    # SS observed 798909407.208879 on device; 809017200.0 in the fixtures.
    assert apple_date(809017200.0) == datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    assert apple_date(0) == datetime(2001, 1, 1, tzinfo=timezone.utc)
    for bad in (None, "2026-08-21T15:00:00Z", True, {"d": 1}):
        assert apple_date(bad) is None


def test_the_report_is_base64_then_json_with_actions_inside():
    rep = read_bundle(_b("typical"))["meetings"][0]["report"]
    assert set(rep) >= {"actions", "decisions", "header", "open_questions", "sentiment", "stoplight"}
    actions = rep["actions"]
    assert len(actions) == 3 and all({"task", "owner", "priority"} <= set(a) for a in actions)
    # two of three omit deadline ENTIRELY (not null), and it is free text
    assert sum("deadline" in a for a in actions) == 1
    assert isinstance(next(a for a in actions if "deadline" in a)["deadline"], str)
    assert decode_report("not base64!!") is None and decode_report(None) is None


def test_casing_is_left_alone():
    m = read_bundle(_b("typical"))["meetings"][0]
    assert "durationSeconds" in m["record"] and "rollingSummary" in m["record"]   # camelCase record
    assert "open_questions" in m["report"] and "technical_issues" in m["report"]  # snake_case report


@pytest.mark.parametrize("name", ["typical", "full", "minimal", "unknown-entry"])
def test_every_fixture_produces_a_page_with_the_card_and_the_title(name):
    b = read_bundle(_b(name))
    html = render_share_page(b, card_title="Card title", card_desc="Card line", transcript_included=True,
                             expires_at="2026-09-21T00:00:00+00:00")
    assert "og:title" in html and "Card title" in html and "noindex" in html
    assert "Northwind Ferry Terminal" in html
    assert "2026-09-21" in html  # expiry stated to the recipient


def test_unknown_entry_is_ignored_not_damage():
    b = read_bundle(_b("unknown-entry"))
    assert len(b["meetings"]) == 1 and b["meetings"][0]["report"] is not None


def test_typical_carries_a_transcript_because_the_exporter_always_does():
    """SS's correction 2026-08-22: there is no transcript toggle in the
    exporter; 12 of 12 real bundles carry one. `typical` now does too and
    the no-transcript case lives in `minimal` as the edge."""
    assert read_bundle(_b("typical"))["meetings"][0]["record"]["transcript"]
    assert read_bundle(_b("minimal"))["meetings"][0]["record"]["transcript"] == ""
    html = render_share_page(read_bundle(_b("minimal")), card_title="t", card_desc="", transcript_included=True, expires_at="2026-09-21")
    assert "Show transcript" not in html


def test_full_shows_transcript_only_when_the_sender_included_it():
    b = read_bundle(_b("full"))
    assert b["meetings"][0]["record"]["transcript"]
    with_ = render_share_page(b, card_title="t", card_desc="", transcript_included=True, expires_at="2026-09-21")
    without = render_share_page(b, card_title="t", card_desc="", transcript_included=False, expires_at="2026-09-21")
    assert "Show transcript" in with_ and "<details" in with_     # tap-to-reveal
    assert "Show transcript" not in without


def test_report_html_is_framed_sandboxed_and_minimal_falls_back_to_fields():
    typical = render_share_page(read_bundle(_b("typical")), card_title="t", card_desc="", transcript_included=False, expires_at="2026-09-21")
    assert 'sandbox=""' in typical and "srcdoc=" in typical
    minimal = render_share_page(read_bundle(_b("minimal")), card_title="t", card_desc="", transcript_included=False, expires_at="2026-09-21")
    # no reportHTML and no report: the page is built from the record's own
    # fields (rollingSummary, sentimentLabel), never framed, never empty
    assert "srcdoc=" not in minimal
    assert "north berth" in minimal and "Focused" in minimal


def test_a_record_with_nothing_at_all_still_says_so():
    b = {"meetings": [{"origin_id": "x", "record": {"title": "Bare"}, "report": None, "started_at": None}]}
    html = render_share_page(b, card_title="t", card_desc="", transcript_included=True, expires_at="2026-09-21")
    assert "Bare" in html and "shared without a report" in html


def test_fallback_page_lists_actions_owners_and_the_one_deadline():
    b = read_bundle(_b("typical"))
    b["meetings"][0]["record"]["reportHTML"] = ""   # force the field path
    html = render_share_page(b, card_title="t", card_desc="", transcript_included=False, expires_at="2026-09-21")
    assert "Action items" in html and "Devendra Rao" in html and "before the pilot starts" in html
    assert html.count("<li>") >= 3


def test_a_non_zip_archive_still_gets_the_card(client, pro_user):
    from tests.test_meeting_shares import HDRS
    resp = client.post("/v1/shares", content=b"this is not a zip", headers={**pro_user["headers"], **HDRS})
    token = resp.json()["url"].rsplit("/", 1)[1]
    page = client.get(f"/s/{token}")
    assert page.status_code == 200 and "og:title" in page.text and "holds no meeting" in page.text


def test_a_real_fixture_through_the_route_renders_the_report(client, pro_user):
    from tests.test_meeting_shares import HDRS
    resp = client.post("/v1/shares", content=_b("typical"),
                       headers={**pro_user["headers"], **HDRS, "Content-Type": "application/zip"})
    token = resp.json()["url"].rsplit("/", 1)[1]
    page = client.get(f"/s/{token}")
    assert page.status_code == 200 and "Northwind Ferry Terminal" in page.text and "srcdoc=" in page.text


# --- The reader is bounded, and bounded DURING extraction (2026-08-22) -----
#
# Raised by CQ after SS found the same shape in their own bundle reader and
# fixed it. This page unzips bytes a client uploaded. The upload is
# authenticated, so it is not open to the world, but an authenticated
# client is not a trusted one, and the blast radius differs from SS's in
# the way that matters: a client-side OOM kills one person's app, a
# server-side one takes GhostPour down for everyone, including every CQ
# call that proxies through us. A deflate bomb is a few kilobytes on the
# wire and unbounded in RAM.
#
# The tests that matter here are the ones that would pass against a
# CHECK-AFTER-READ implementation and still leave the hole open, so they
# assert the bound is respected on a payload whose declared size is a lie.

import io as _io
import zipfile
import zipfile as _zipfile

import pytest as _pytest

from app.services.share_bundle import (
    MAX_ENTRIES, MAX_ENTRY_BYTES, ShareBundleTooLarge, read_bundle as _read,
)


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_a_deflate_bomb_does_not_become_a_page():
    """A highly compressible entry past the per-entry cap. Tiny on the wire,
    not tiny in memory. The entry is skipped and the rest of the bundle
    still renders, the same degradation as any other unreadable entry.

    The bomb is VALID JSON on purpose. An earlier version filled it with
    null bytes, and that test could not fail: unbounded, the 33 MB is read,
    the JSON parse fails, the record is dropped, and `meetings == []` is
    true either way. Green whether or not the bomb was decompressed. Caught
    by sabotage, not by reading it. Valid JSON means an unbounded reader
    produces a meeting here and a bounded one does not, so the assertion
    finally distinguishes them."""
    bomb = b'{"title": "' + b"x" * (MAX_ENTRY_BYTES + 1024 * 1024) + b'"}'
    payload = _zip({
        "manifest.json": b'{"formatVersion": 1}',
        "meetings/AAAA.json": bomb,
    })
    del bomb
    assert len(payload) < 200_000, "fixture is not actually a bomb"
    out = _read(payload)
    assert out["manifest"] == {"formatVersion": 1}
    assert out["meetings"] == [], "the oversized entry was decompressed anyway"


def test_the_bomb_is_never_allocated_in_the_first_place():
    """The assertion that actually distinguishes a fix from a comfortable
    test: PEAK MEMORY, not the return value.

    An earlier version of this test patched the zip header to lie about the
    uncompressed size and accepted BadZipFile as a pass. That test could not
    fail on the bug it was written for: a truncated read trips zipfile's own
    CRC check, so it took the exception path and never reached its
    assertion. Green, and blind. Measuring allocation instead cannot pass
    vacuously, because a reader that decompresses 300 MB has to put it
    somewhere.
    """
    import tracemalloc
    bomb = b"\x00" * (300 * 1024 * 1024)
    payload = _zip({"manifest.json": b'{"formatVersion": 1}',
                    "meetings/AAAA.json": bomb})
    assert len(payload) < 400_000, "fixture is not actually a bomb"
    del bomb

    tracemalloc.start()
    try:
        out = _read(payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert out["meetings"] == []
    assert peak < 64 * 1024 * 1024, (
        f"peaked at {peak / 1048576:.0f} MB decompressing a 400 KB upload")


def test_the_streaming_check_holds_when_the_declared_size_is_not_the_bound():
    """The declared size is a free fast path and must not be the only one:
    a crafted central directory can claim anything. Raising the per-entry
    cap above the declared size takes that fast path out of play, so the
    only thing left between a 300 MB entry and the heap is the check inside
    the read loop. Peak memory is again the assertion, because a
    check-AFTER-read implementation passes every other one."""
    import tracemalloc
    from app.services import share_bundle as sb
    bomb = b"\x00" * (300 * 1024 * 1024)
    payload = _zip({"manifest.json": b'{"formatVersion": 1}',
                    "meetings/AAAA.json": bomb})
    del bomb

    entry_cap, total_cap = sb.MAX_ENTRY_BYTES, sb.MAX_TOTAL_READ_BYTES
    sb.MAX_ENTRY_BYTES = 400 * 1024 * 1024   # the fast path cannot fire
    sb.MAX_TOTAL_READ_BYTES = 8 * 1024 * 1024
    tracemalloc.start()
    try:
        out = sb.read_bundle(payload)
        _, peak = tracemalloc.get_traced_memory()
    except zipfile.BadZipFile:
        # Stopping mid-entry can trip zipfile's own CRC check on close. That
        # is a correct outcome, but it is NOT what is under test, so measure
        # and assert rather than returning.
        _, peak = tracemalloc.get_traced_memory()
        out = {"meetings": []}
    finally:
        tracemalloc.stop()
        sb.MAX_ENTRY_BYTES, sb.MAX_TOTAL_READ_BYTES = entry_cap, total_cap

    assert out["meetings"] == []
    assert peak < 64 * 1024 * 1024, (
        f"peaked at {peak / 1048576:.0f} MB with the declared size out of play")


def test_too_many_entries_is_refused_outright():
    """The other axis. Ten thousand small entries is not a bomb by size and
    is still a way to spend a process."""
    payload = _zip({f"meetings/{i:06d}.json": b"{}" for i in range(MAX_ENTRIES + 1)})
    with _pytest.raises(ShareBundleTooLarge):
        _read(payload)


def test_a_normal_bundle_is_completely_unaffected():
    """The bound must be invisible to every real share. SS's own fixtures,
    through the same path, unchanged."""
    for name in ("typical", "full", "minimal", "unknown-entry"):
        out = _read(_b(name))
        assert out["manifest"] is not None, f"{name} stopped parsing"
        assert out["meetings"], f"{name} lost its meetings"


def test_the_total_budget_is_shared_across_entries():
    """Per-entry bounds alone are not enough: many entries each just under
    the cap add up. Proven by shrinking the budget rather than by building
    a four-gigabyte fixture, so the test stays cheap and still exercises
    the running total."""
    from app.services import share_bundle as sb
    entries = {"manifest.json": b'{"formatVersion": 1}'}
    for i in range(4):
        entries[f"meetings/{i:04d}.json"] = b'{"title": "' + b"x" * 20_000 + b'"}'
    payload = _zip(entries)
    original = sb.MAX_TOTAL_READ_BYTES
    sb.MAX_TOTAL_READ_BYTES = 30_000  # room for about one record
    try:
        out = sb.read_bundle(payload)
    finally:
        sb.MAX_TOTAL_READ_BYTES = original
    assert len(out["meetings"]) < 4, "the running total was not enforced"
