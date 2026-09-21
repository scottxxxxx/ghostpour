"""The Jev judge panel on the Performance tab: /admin/typesafe-health.

Scott, 2026-09-20: "we also need to add a way to monitor the jev performance
and failures in our dashboard." Rows are written through the real recorder
into the test database, so the recorder, the table and the endpoint are one
path here and a column the three disagree on fails.
"""

import asyncio
import sqlite3

import pytest

from app.services import typesafe_judge as tj

ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/typesafe-health"


def _rec(row, app_id="shouldersurf"):
    base = {"judgment": "offer_reply", "mode": "primary"}
    asyncio.run(tj.record({**base, **row}, app_id))


@pytest.fixture
def seeded(client):
    # 6 reached Jev: 4 ok, 1 unsure, 1 error. Then 2 the breaker skipped.
    for ms in (200, 300, 400, 500):
        _rec({"outcome": "ok", "fell_back": False, "jev_ms": ms, "input_tokens": 1000,
              "confidence": 0.99})
    _rec({"outcome": "low_confidence", "fell_back": True, "jev_ms": 350, "fallback_ms": 900,
          "input_tokens": 1000, "confidence": 0.43})
    _rec({"outcome": "error", "fell_back": True, "jev_ms": 2000, "fallback_ms": 950,
          "error_type": "ConnectError"})
    _rec({"outcome": "breaker_open", "fell_back": True, "fallback_ms": 1000})
    _rec({"outcome": "breaker_open", "fell_back": True, "fallback_ms": 1000})
    # Another app, shadow mode: 2 compared (1 agreed), 1 where Haiku failed open.
    _rec({"mode": "shadow", "outcome": "ok", "jev_ms": 250, "agreed": True}, "techrehearsal")
    _rec({"mode": "shadow", "outcome": "ok", "jev_ms": 260, "agreed": False}, "techrehearsal")
    _rec({"mode": "shadow", "outcome": "ok", "jev_ms": 270, "agreed": None}, "techrehearsal")
    return client


def test_the_recorder_writes_what_it_was_given_and_no_user_text(seeded, tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    cols = [r[1] for r in conn.execute("pragma table_info(typesafe_calls)")]
    assert not {"reply", "offer", "state", "text"} & set(cols)
    n, cost = conn.execute("select count(*), sum(cost_usd) from typesafe_calls").fetchone()
    assert n == 11
    # 5 rows carried 1000 input tokens at $0.042 per million.
    assert cost == pytest.approx(5 * 1000 * 0.042 / 1_000_000)
    assert conn.execute("select agreed from typesafe_calls where mode='shadow' "
                        "order by jev_ms").fetchall() == [(1,), (0,), (None,)]


def test_a_record_that_cannot_be_written_raises_nowhere(monkeypatch):
    from app import database
    monkeypatch.setattr(database, "_db_path", "/nonexistent-dir/nope.db")
    asyncio.run(tj.record({"judgment": "offer_reply", "mode": "primary", "outcome": "ok"}, None))


def test_failure_rate_is_over_the_calls_that_reached_jev(seeded):
    s = seeded.get(URL, params={"app": "shouldersurf"}, headers=ADMIN).json()["summary"]
    assert s["attempts"] == 8 and s["asked"] == 6 and s["failed"] == 1
    # 1 of 6, NOT 1 of 8: the two skipped calls never reached Jev, and
    # counting them would shrink the rate during the outage it measures.
    assert s["failure_rate"] == round(1 / 6, 4)
    assert s["fell_back"] == 4
    assert s["outcomes"] == {"ok": 4, "low_confidence": 1, "error": 1, "breaker_open": 2}


def test_latency_is_over_answers_only(seeded):
    s = seeded.get(URL, params={"app": "shouldersurf"}, headers=ADMIN).json()["summary"]
    # 200, 300, 350, 400, 500. The 2000ms error is a wait, not an answer.
    assert s["jev_p50_ms"] == 350 and s["jev_p95_ms"] == 500


def test_agreement_skips_rows_where_haiku_gave_no_verdict(seeded):
    s = seeded.get(URL, params={"app": "techrehearsal"}, headers=ADMIN).json()["summary"]
    assert s["shadow_compared"] == 2 and s["shadow_agreed"] == 1


def test_the_app_filter_scopes_rows_and_no_filter_is_every_app(seeded):
    assert seeded.get(URL, headers=ADMIN).json()["summary"]["attempts"] == 11
    assert seeded.get(URL, params={"app": "techrehearsal"},
                      headers=ADMIN).json()["summary"]["attempts"] == 3


def test_recent_failures_lists_failures_and_skips_newest_first(seeded):
    f = seeded.get(URL, headers=ADMIN).json()["recent_failures"]
    assert [r["outcome"] for r in f] == ["breaker_open", "breaker_open", "error"]
    assert f[-1]["error_type"] == "ConnectError"


def test_process_state_reports_mode_and_the_live_breaker(seeded, monkeypatch):
    b = tj.Breaker()
    for _ in range(3):
        b.failure("ConnectError")
    monkeypatch.setattr(tj, "breaker", b)
    p = seeded.get(URL, headers=ADMIN).json()["process"]
    assert p["breaker"]["open"] is True and p["breaker"]["consecutive_failures"] == 3
    assert p["breaker"]["last_error"] == "ConnectError"
    assert p["mode"] == "off" and p["model"] == tj.MODEL


def test_an_empty_window_divides_nothing(client):
    d = client.get(URL, headers=ADMIN).json()
    assert d["summary"]["attempts"] == 0 and d["summary"]["failure_rate"] is None
    assert d["by_day"] == [] and d["recent_failures"] == []


def test_it_needs_the_admin_key(client):
    assert client.get(URL, headers={"X-Admin-Key": "wrong"}).status_code in (401, 403)


def test_the_dashboard_panel_calls_this_endpoint_with_the_shared_app_filter():
    html = open("app/static/admin.html").read()
    fn = html[html.index("async function loadJevHealth()"):html.index("function renderJevHealth(")]
    assert "/webhooks/admin/typesafe-health" in fn
    assert "getElementById('app')" in fn, "the panel must use the SHARED app filter"
    assert "loadJevHealth();" in html[html.index("async function loadLatency()"):
                                      html.index("async function loadJevHealth()")]
