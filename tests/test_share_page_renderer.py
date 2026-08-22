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
