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


# --- The card ATTRIBUTES survive an apostrophe (2026-08-22) ----------------
#
# CQ checked escaping on this page by grepping for HTML entities, found
# `&#39;` nine times, and concluded the meta attributes escape. SS traced
# where those nine actually were: one in the <h1>, eight in the transcript
# <pre>, and ZERO in any content='...' attribute. So escaping was proven
# for the BODY, which is archive-sourced, and entirely unproven for the
# ATTRIBUTES, which are single-quoted and are exactly where an apostrophe
# could terminate the value early. CQ corrected themselves out loud; this
# is the test that means nobody has to grep for it again.
#
# It matters more than it sounds. `og:title` is what iMessage, Slack and
# every unfurler render, so this failure lands on the card that a
# recipient sees BEFORE they open anything, and it lands as a truncated or
# malformed tag rather than as an error. And an apostrophe is not an
# exotic input: "Sarah's team sync" is what a Tuesday meeting is called.
#
# Asserted through a real HTML parser rather than by searching for the
# escaped string, because "the entity appears somewhere" is the check that
# already failed once here. A parser reports the attribute VALUE, so an
# attribute terminated early comes back truncated and the assertion fails.

import html.parser as _htmlparser


class _Attrs(_htmlparser.HTMLParser):
    """Collects meta content by property/name, and the <title> text."""

    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "meta":
            key = d.get("property") or d.get("name")
            if key and "content" in d:
                self.meta[key] = d["content"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data


def _parsed(html_text: str) -> _Attrs:
    p = _Attrs()
    p.feed(html_text)
    return p


APOSTROPHE_TITLES = [
    "Sarah's team sync",
    "Q3 planning, don't reschedule",
    "L'équipe: Séverine's review",
    'He said "ship it" and left',
    "Tom & Sarah's <review>",
]


@_pytest.mark.parametrize("title", APOSTROPHE_TITLES)
def test_the_card_title_attribute_is_not_terminated_early(title):
    from app.services.share_bundle import render_share_page
    page = render_share_page({"meetings": []}, card_title=title, card_desc="",
                             transcript_included=False, expires_at="2026-09-21T00:00:00Z")
    got = _parsed(page)
    assert got.title == title, f"<title> came back {got.title!r}"
    for key in ("og:title", "twitter:title"):
        assert got.meta.get(key) == title, (
            f"{key} came back {got.meta.get(key)!r}, so the attribute was "
            "cut short or the tag was malformed")


@_pytest.mark.parametrize("desc", APOSTROPHE_TITLES)
def test_the_card_description_attribute_survives_the_same_characters(desc):
    """The description is the other half of what an unfurler renders, and it
    is a different variable through the same template, so a fix that only
    covered the title would leave this one open."""
    from app.services.share_bundle import render_share_page
    page = render_share_page({"meetings": []}, card_title="x", card_desc=desc,
                             transcript_included=False, expires_at="2026-09-21T00:00:00Z")
    got = _parsed(page)
    for key in ("og:description", "twitter:description"):
        assert got.meta.get(key) == desc, f"{key} came back {got.meta.get(key)!r}"


def test_an_apostrophe_title_cannot_inject_a_new_attribute():
    """The reason this is not merely cosmetic. If the value terminates
    early, everything after it is parsed as MARKUP, so a title is an
    injection point into the head of a page other people load."""
    from app.services.share_bundle import render_share_page
    hostile = "x' onload='alert(1)"
    page = render_share_page({"meetings": []}, card_title=hostile, card_desc="",
                             transcript_included=False, expires_at="2026-09-21T00:00:00Z")
    got = _parsed(page)
    assert got.meta.get("og:title") == hostile
    assert "onload" not in page.lower().replace("&#39;", "'").split("<body")[0].replace(hostile, "")


# --- The route for a recipient WITHOUT the app (2026-08-23) ----------------
#
# Scott's actual goal, stated plainly: send a meeting in iMessage, and if
# the recipient has Shoulder Surf it opens there, if they do not they can
# get it, and either way they can read the report.
#
# The first and third were built. The SECOND did not exist at all: the page
# had no App Store link, no banner, and no way into the app. A universal
# link on a device without the app does not offer the App Store on its own,
# it silently opens the web page, so a recipient without the app read the
# meeting and was never told the app existed.
#
# Two mechanisms because they reach different people. `apple-itunes-app` is
# Apple's own banner and is the ONLY thing that can tell installed from not
# installed, which a web page cannot detect and must not guess: iOS renders
# "Open" or "Get" itself. The visible link covers everyone Safari's banner
# does not, which is Chrome on iOS, Android, and every desktop browser,
# and that last one is where a link pasted into Slack gets opened.

APP_ID = "6760098225"
SHARE_URL = "https://share.shouldersurf.com/s/AAAAAAAAAAAAAAAAAAAAAA"


def _page(**kw):
    from app.services.share_bundle import render_share_page
    base = dict(card_title="Northwind rollout", card_desc="A line.",
                transcript_included=False, expires_at="2026-09-30T00:00:00Z")
    return render_share_page({"meetings": []}, **{**base, **kw})


def test_the_smart_banner_carries_the_app_id_and_this_share():
    """app-argument is what makes "Open" land on THIS meeting instead of
    the app's home screen, so it is not decoration."""
    html = _page(app_store_id=APP_ID, share_url=SHARE_URL)
    got = _parsed(html)
    assert got.meta.get("apple-itunes-app") == f"app-id={APP_ID},app-argument={SHARE_URL}"


def test_the_banner_is_still_valid_without_a_share_url():
    """Malformed is worse than minimal: `app-id=X,app-argument=` would be a
    banner pointing the app at nothing."""
    html = _page(app_store_id=APP_ID)
    assert _parsed(html).meta.get("apple-itunes-app") == f"app-id={APP_ID}"


def test_a_visible_store_link_exists_for_browsers_safari_cannot_reach():
    """The banner is Safari-only. Without this, a link opened in Chrome or
    on a desktop has no route to the app at all, which is most of the
    places a shared link actually gets opened."""
    html = _page(app_store_id=APP_ID, share_url=SHARE_URL)
    assert f"https://apps.apple.com/app/id{APP_ID}" in html
    assert "Open in Shoulder Surf" in html


def test_no_app_store_id_means_no_banner_and_no_dead_link():
    """The right failure. A store link that 404s on a page a stranger
    opens is worse than a page that simply does not mention the app."""
    html = _page()
    assert "apple-itunes-app" not in html
    assert "apps.apple.com" not in html
    assert "Open in Shoulder Surf" not in html


def test_the_store_route_never_replaces_the_report():
    """Scott's third case has to survive the second. Whatever the page
    offers about the app, the meeting is still readable on it."""
    from app.services.share_bundle import read_bundle, render_share_page
    b = read_bundle(_b("typical"))
    html = render_share_page(b, card_title="T", card_desc="D", transcript_included=True,
                             expires_at="2026-09-30T00:00:00Z",
                             app_store_id=APP_ID, share_url=SHARE_URL)
    assert "apps.apple.com" in html
    assert "<iframe" in html, "the report stopped rendering once the store link was added"
    assert "Show transcript" in html
