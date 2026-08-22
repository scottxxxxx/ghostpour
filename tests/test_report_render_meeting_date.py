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
