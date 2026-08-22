"""POST /v1/reports/render dates the header from the meeting, not the clock.

Found 2026-08-22 while grepping GP for the bug shape CQ flagged on their
side: anything that dates a meeting from a timestamp that is really an
ingest or a render clock. The dossier heading was one (fixed separately).
This was the other, and it is the worse of the two, because the dossier
goes into a prompt while this goes into a document the user keeps.

`RenderRequest` carried only report_json and duration_seconds, so the
route had no way to know when the meeting was and stamped the header with
`datetime.now()`. Record a meeting on Monday, edit the report on
Wednesday, get a document headed Wednesday. Nothing errors, and the user
has no way to tell.

What is fixed here: the route ACCEPTS the meeting start, same field name
and format the generate path already takes, so a client that sends it
gets the right date. What is NOT fixed here, deliberately: what to render
when it is absent. CQ recommends no date over an invented one and I agree,
but that is a product ruling and it is Scott's. Until he makes it the
fallback still fabricates, and it now says so in the log, which is the
part that makes the ruling measurable instead of theoretical.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.dependencies import get_current_user
from app.services.meeting_report import format_meeting_date
from app.main import app
from app.models.user import UserRecord

USER = "user-render-1"
REPORT_JSON = {"header": {"title": "Kickoff"}, "actions": [], "decisions": []}


def _user() -> UserRecord:
    return UserRecord(
        id=USER, apple_sub="sub_render", tier="pro",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")


@pytest.fixture
def render_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _render(render_client, **extra):
    body = {"report_json": REPORT_JSON, "duration_seconds": 1800, **extra}
    resp = render_client.post("/v1/reports/render", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["report_html"]


def test_the_header_uses_the_meeting_start_not_today(render_client):
    """The whole point. A meeting from three days ago, re-rendered now,
    must still be headed three days ago."""
    html = _render(render_client, meeting_start_iso="2026-08-11T13:01:00-05:00")
    assert "August 11, 2026" in html
    assert "1:01 PM" in html
    # And the render clock must not be in there instead. Skipped only on the
    # one day a year it would collide, rather than weakened into a tautology.
    today = format_meeting_date(datetime.now(timezone.utc), "en")
    if today != "August 11, 2026":
        assert today not in html


def test_a_sent_timezone_abbreviation_reaches_the_header(render_client):
    html = _render(render_client, meeting_start_iso="2026-08-11T13:01:00-05:00",
                   timezone_abbr="CST")
    assert "CST" in html


def test_an_unparseable_start_does_not_500(render_client, caplog):
    """Garbage in the field degrades to the old behaviour rather than
    taking down a live-preview keystroke, and it is logged as a
    fabrication so it cannot be mistaken for a real date later."""
    with caplog.at_level("INFO"):
        html = _render(render_client, meeting_start_iso="not-a-date")
    assert html
    assert "report_render_date_fabricated" in caplog.text


def test_no_meeting_start_still_renders_and_says_it_fabricated(caplog, render_client):
    """The behaviour Scott has to rule on. It is UNCHANGED by this PR: a
    client that sends nothing gets the render clock, exactly as before. The
    log line is the new part, and it is what makes "how often does this
    actually fire" answerable before changing what users see."""
    with caplog.at_level("INFO"):
        html = _render(render_client)
    assert html
    assert "report_render_date_fabricated" in caplog.text


def test_sending_the_start_does_not_log_a_fabrication(caplog, render_client):
    """The negative half, so the log line means something. Without this a
    logger that fired unconditionally would satisfy every test above."""
    with caplog.at_level("INFO"):
        _render(render_client, meeting_start_iso="2026-08-11T13:01:00-05:00")
    assert "report_render_date_fabricated" not in caplog.text


# --- The offset is load bearing, and GP does not flatten it (2026-08-22) ---
#
# SS found a live drift while wiring this field: two of their call sites
# disagreed about the FORMAT of meeting_start_iso. One sent a local offset,
# the other a bare ISO8601DateFormatter, which defaults to UTC. Both are
# valid ISO 8601 and both name the same INSTANT, so nothing ever errored.
# They disagree about which DAY it was, which is the only thing a header
# date exists to say: a 9pm meeting west of Greenwich went up stamped with
# tomorrow's date from one path and today's from the other.
#
# Their fix only works if GP renders the day the offset names. If GP
# normalised to UTC before formatting, the bug would simply move to our
# side of the same line and their fix would be a no-op. CQ asked us to
# check the FORMATTING path and not just the parse, because
# datetime.fromisoformat can preserve tzinfo perfectly and a strftime three
# lines later can still throw it away.
#
# It does not: format_meeting_date reads dt.year/month/day off the aware
# datetime and never calls astimezone. These two tests are the echo, so
# that stays true. SS cannot see this property from their end (a 200 from a
# GP that preserves the offset and a 200 from a GP that flattens it are
# identical responses), which makes this exactly the claim that has to be
# proved on our side. Rule 4, with the roles reversed.

def test_an_evening_meeting_west_of_greenwich_keeps_its_own_day(render_client):
    """21:30 at UTC-5 is 02:30 the NEXT day in UTC. The header must say the
    11th, which is the day it was for the people in the room."""
    html = _render(render_client, meeting_start_iso="2026-08-11T21:30:00-05:00")
    assert "August 11, 2026" in html
    assert "August 12, 2026" not in html
    assert "9:30 PM" in html
    assert "2:30 AM" not in html


def test_the_day_comes_from_the_offset_the_client_sent(render_client):
    """The same INSTANT, sent in the UTC form SS's second call site was
    using, renders as the 12th. That is not GP being wrong: it is GP being
    faithful to what it was told, which is why the format is the client's
    to get right and why SS's drift was a real bug rather than cosmetic.
    Pinned so nobody later "fixes" this into a normalisation."""
    html = _render(render_client, meeting_start_iso="2026-08-12T02:30:00+00:00")
    assert "August 12, 2026" in html
    assert "August 11, 2026" not in html


# SS's addition, and it is the better fixture: a UTC normalisation flips the
# day ONLY near midnight and ONLY in one direction, so it cannot show up in
# any test written at a round hour in the middle of the day, which is what
# everyone writes by hand. That is why their drift survived for an unknown
# length of time. The code was not subtle; the fixture was comfortable.
#
# Two directions, deliberately, so a pass can be told apart from a
# coincidence: a naive UTC path gets the negative-offset case wrong by
# rolling forward and the positive-offset case wrong by rolling back. One
# red and one green localises the break; both green means the path is
# offset preserving end to end.

def test_2330_at_a_negative_offset_keeps_its_local_day(render_client):
    """23:30 at UTC-7 is 06:30 the next day in UTC."""
    html = _render(render_client, meeting_start_iso="2026-08-21T23:30:00-07:00")
    assert "August 21, 2026" in html
    assert "August 22, 2026" not in html
    assert "11:30 PM" in html


def test_0030_at_a_positive_offset_keeps_its_local_day(render_client):
    """00:30 at UTC+9 is 15:30 the PREVIOUS day in UTC, so a naive path
    breaks this one in the opposite direction from the case above."""
    html = _render(render_client, meeting_start_iso="2026-08-22T00:30:00+09:00")
    assert "August 22, 2026" in html
    assert "August 21, 2026" not in html
    assert "12:30 AM" in html
