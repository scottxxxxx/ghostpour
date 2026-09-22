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


# --- marks per turn and the per-judgment trend (Scott, 2026-09-22) -------------------------

@pytest.fixture
def with_marks(seeded):
    # Four N-400 evidence turns Jev answered: 3 facts / 1 marked, 2 / 0,
    # 1 / 1, 2 / 2. And one it did not answer: checked 2, marked None.
    for checked, marked in ((3, 1), (2, 0), (1, 1), (2, 2)):
        _rec({"judgment": "n400_evidence_support", "outcome": "ok", "jev_ms": 300,
              "input_tokens": 800, "facts_checked": checked, "facts_marked": marked}, "n400")
    _rec({"judgment": "n400_evidence_support", "outcome": "timeout", "fell_back": False,
          "jev_ms": 10000, "error_type": "ReadTimeout", "facts_checked": 2, "facts_marked": None}, "n400")
    return seeded


def test_the_recorder_writes_the_fact_counts(with_marks, tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    rows = conn.execute("select facts_checked, facts_marked from typesafe_calls "
                        "where judgment='n400_evidence_support' order by facts_checked, facts_marked").fetchall()
    assert rows == [(1, 1), (2, None), (2, 0), (2, 2), (3, 1)]
    assert conn.execute("select facts_checked from typesafe_calls where judgment='offer_reply'").fetchall() == [(None,)] * 11


def test_marks_per_turn_is_over_the_turns_jev_answered(with_marks):
    d = with_marks.get(URL, params={"app": "n400"}, headers=ADMIN).json()
    m = d["summary"]["marks"]
    assert m == {"turns_judged": 4, "turns_with_a_mark": 3, "mark_rate_per_turn": 0.75,
                 "facts_checked": 8, "facts_marked": 4, "mark_rate_per_fact": 0.5}
    (j,) = [r for r in d["by_judgment"] if r["judgment"] == "n400_evidence_support"]
    assert j["marks"]["turns_judged"] == 4, "the timed-out turn is not a turn with zero marks"


def test_a_judgment_with_no_facts_has_no_mark_rate(with_marks):
    d = with_marks.get(URL, params={"app": "shouldersurf"}, headers=ADMIN).json()
    assert d["summary"]["marks"] == {"turns_judged": 0, "turns_with_a_mark": 0, "mark_rate_per_turn": None,
                                     "facts_checked": 0, "facts_marked": 0, "mark_rate_per_fact": None}


def test_each_judgment_carries_a_trend_aligned_to_the_buckets(with_marks):
    d = with_marks.get(URL, params={"days": 3, "bucket": "hour"}, headers=ADMIN).json()
    assert d["bucket"] == "hour"
    keys = [b["bucket"] for b in d["buckets"]]
    assert len(keys) >= 3 * 24 and all(len(k) == 19 for k in keys)
    for j in d["by_judgment"]:
        t = j["trend"]
        assert set(t) == {"n", "p50", "p95", "failed", "turns_with_a_mark"}
        assert all(len(t[k]) == len(keys) for k in t)
        assert sum(t["n"]) == j["asked"] - j["failed"], "the trend's n is the answered calls, like the models' charts"
    (ev,) = [r for r in d["by_judgment"] if r["judgment"] == "n400_evidence_support"]
    assert sum(ev["trend"]["n"]) == 4 and sum(ev["trend"]["failed"]) == 1 and sum(ev["trend"]["turns_with_a_mark"]) == 3
    assert ev["n"] == 5
    assert with_marks.get(URL, params={"bucket": "week"}, headers=ADMIN).status_code == 400


def test_the_evidence_check_records_what_it_asked_and_what_it_marked(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import n400_evidence_support as es
    checked = [{"field_id": f, "question": "q", "applicant_said": "s", "cited_words": "c",
                "recorded_answer": "v", "options": ["v", "w"]} for f in ("a", "b", "c")]
    body = {"answers": {"f0": {"choice": "insufficient", "confidence": 0.9},
                        "f1": {"choice": "supports", "confidence": 0.9},
                        "f2": {"choice": "contradicts", "confidence": 0.2}}}   # under the floor
    monkeypatch.setattr(tj, "guarded_ask", AsyncMock(return_value=(body, {"judgment": "x", "mode": "primary", "outcome": "ok"})))
    seen = []
    monkeypatch.setattr(tj, "record_later", lambda row, app_id: seen.append(row))
    asyncio.run(es._judge("k", checked, "primary", "n400", "t_1"))
    assert seen[0]["facts_checked"] == 3 and seen[0]["facts_marked"] == 1
    monkeypatch.setattr(tj, "guarded_ask", AsyncMock(return_value=(None, {"judgment": "x", "mode": "primary", "outcome": "timeout"})))
    asyncio.run(es._judge("k", checked, "primary", "n400", "t_2"))
    assert seen[1]["facts_checked"] == 3 and seen[1]["facts_marked"] is None


def test_the_panel_draws_marks_and_the_same_multiples_as_the_models():
    html = open("app/static/admin.html").read()
    fn = html[html.index("function renderJevHealth("):html.index("function renderJevHealth(") + 9000]
    assert "mark_rate_per_turn" in fn and "Marks per turn" in fn
    assert "renderPerfMultiples('jev-multiples'" in fn, "the judgment charts must be the models' renderer, not a second one"
    load = html[html.index("async function loadJevHealth()"):html.index("function renderJevHealth(")]
    assert "params.set('bucket'" in load and "lat-bucket" in load
    assert 'id="jev-multiples"' in html
