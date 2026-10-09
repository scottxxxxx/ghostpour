"""Meetings in progress on the dashboard (Scott, 2026-10-09), and the three
additive ping fields agreed with SS the same day: meeting_heartbeat (with
`paused`), stop_reason on meeting_stop, resumed_from_meeting_id on
meeting_start.

A meeting is in progress when its start has no stop. One start in four never
gets a stop (118 of 469 in the 30 days to 2026-10-09), so the view says how
sure it is: heartbeat, usage row, or nothing, and never shows a start older
than the window as running."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

ADMIN = {"X-Admin-Key": "test-admin-key"}
SS = {"X-App-ID": "shouldersurf"}


def _u() -> str:
    return str(uuid.uuid4())


def _iso(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _ping(client, **body):
    body.setdefault("device_id", _u())
    headers = body.pop("headers", SS)
    r = client.post("/v1/events/ping", json=body, headers=headers)
    assert r.status_code == 204, r.text
    return body


def _sql(db, sql, *args):
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _rows(db, sql, *args):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _start(client, db, minutes_ago=0.0, **over):
    """A meeting_start received minutes_ago (the ping is stamped now, so the
    row is backdated afterwards, the only way to age a start in a test)."""
    body = _ping(client, event_type="meeting_start", meeting_id=_u(),
                 app_version="1.19", app_build="2201", device_model="iPhone17,2", **over)
    if minutes_ago:
        _sql(db, "UPDATE telemetry_events SET received_at = ? WHERE meeting_id = ? "
                 "AND event_type = 'meeting_start'", _iso(minutes_ago), body["meeting_id"])
    return body


def _beat(client, db, start, minutes_ago=0.0, **over):
    _ping(client, event_type="meeting_heartbeat", meeting_id=start["meeting_id"],
          device_id=start["device_id"], **over)
    if minutes_ago:
        _sql(db, "UPDATE meeting_heartbeats SET last_at = ? WHERE meeting_id = ?",
             _iso(minutes_ago), start["meeting_id"])


def _heard(db, start, minutes_ago, prompt_mode="AutoSummary", phone_seconds=None):
    _sql(db, "INSERT INTO usage_log (id, user_id, app_id, provider, model, estimated_cost_usd, "
             "request_timestamp, status, call_type, prompt_mode, meeting_id, session_duration_sec) "
             "VALUES (?, 'u', 'shouldersurf', 'anthropic', 'm', 0.01, ?, 'success', 'summary', ?, ?, ?)",
         _u(), _iso(minutes_ago), prompt_mode, start["meeting_id"], phone_seconds)


def _live(client, **params):
    r = client.get("/webhooks/admin/meetings/live", params=params, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _one(client, start):
    d = _live(client)
    rows = [m for m in d["meetings"] if m["meeting_id"] == start["meeting_id"]]
    assert len(rows) == 1, d
    return rows[0]


# --- the ping side ---------------------------------------------------------


def test_heartbeats_upsert_one_row_per_meeting(client, tmp_db_path):
    start = _start(client, tmp_db_path)
    for secs, paused in ((60, False), (120, False), (180, True)):
        _beat(client, tmp_db_path, start, duration_seconds=secs, paused=paused)
    rows = _rows(tmp_db_path, "SELECT beats, duration_seconds, paused, device_id, app_id "
                              "FROM meeting_heartbeats WHERE meeting_id = ?", start["meeting_id"])
    assert rows == [(3, 180, 1, start["device_id"], "shouldersurf")]
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM telemetry_events WHERE event_type = "
                              "'meeting_heartbeat'") == [(0,)], "a beat is never an event row"


def test_a_heartbeat_without_a_meeting_id_is_accepted_and_stores_nothing(client, tmp_db_path):
    _ping(client, event_type="meeting_heartbeat", duration_seconds=60)
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM meeting_heartbeats") == [(0,)]


def test_stop_reason_and_resumed_from_are_stored_on_their_own_events(client, tmp_db_path):
    first = _start(client, tmp_db_path)
    second = _start(client, tmp_db_path, resumed_from_meeting_id=first["meeting_id"])
    _ping(client, event_type="meeting_stop", meeting_id=first["meeting_id"],
          duration_seconds=300, stop_reason="recovered")
    assert _rows(tmp_db_path, "SELECT resumed_from_meeting_id FROM telemetry_events WHERE meeting_id = ? "
                              "AND event_type = 'meeting_start'", second["meeting_id"]) == [(first["meeting_id"],)]
    assert _rows(tmp_db_path, "SELECT stop_reason FROM telemetry_events WHERE meeting_id = ? "
                              "AND event_type = 'meeting_stop'", first["meeting_id"]) == [("recovered",)]


def test_an_unknown_stop_reason_is_refused(client):
    r = client.post("/v1/events/ping", headers=SS, json={
        "event_type": "meeting_stop", "device_id": _u(), "meeting_id": _u(), "stop_reason": "magic"})
    assert r.status_code == 422


# --- the live view -----------------------------------------------------------


def test_live_needs_the_admin_key(client):
    r = client.get("/webhooks/admin/meetings/live", headers={"X-Admin-Key": "nope"})
    assert r.status_code == 403


def test_a_fresh_heartbeat_is_live_with_the_phones_clock(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=12)
    _beat(client, tmp_db_path, start, duration_seconds=700)
    m = _one(client, start)
    assert (m["state"], m["signal"], m["phone_seconds"]) == ("live", "heartbeat", 700)
    assert 11 * 60 <= m["running_seconds"] <= 13 * 60
    assert m["build_heartbeats"] is True


def test_a_paused_heartbeat_is_paused(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=5)
    _beat(client, tmp_db_path, start, duration_seconds=240, paused=True)
    assert _one(client, start)["state"] == "paused"


def test_a_heartbeat_gone_silent_is_quiet_not_dead(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=30)
    _beat(client, tmp_db_path, start, minutes_ago=10, duration_seconds=1200)
    m = _one(client, start)
    assert (m["state"], m["signal"]) == ("quiet", "heartbeat")


def test_a_young_meeting_with_no_signal_is_early(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=2)
    m = _one(client, start)
    assert (m["state"], m["signal"], m["last_heard_at"]) == ("early", "none", None)


def test_an_old_build_heard_through_autosummary_is_live(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=40)
    _heard(tmp_db_path, start, minutes_ago=5, phone_seconds=2100)
    m = _one(client, start)
    assert (m["state"], m["signal"], m["phone_seconds"], m["last_heard_kind"]) == (
        "live", "usage", 2100, "AutoSummary")
    assert m["build_heartbeats"] is False


def test_an_old_build_not_heard_for_twice_the_summary_interval_is_quiet(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=60)
    _heard(tmp_db_path, start, minutes_ago=25)
    assert _one(client, start)["state"] == "quiet"


def test_a_usage_row_from_before_the_start_does_not_count(client, tmp_db_path):
    """A meeting_id reused across post-meeting chat must not revive a meeting."""
    start = _start(client, tmp_db_path, minutes_ago=40)
    _heard(tmp_db_path, start, minutes_ago=50)
    m = _one(client, start)
    assert (m["state"], m["signal"]) == ("quiet", "none")


def test_a_stopped_meeting_is_gone_and_a_recovered_stop_is_counted(client, tmp_db_path):
    start = _start(client, tmp_db_path, minutes_ago=20)
    _ping(client, event_type="meeting_stop", meeting_id=start["meeting_id"],
          duration_seconds=1100, stop_reason="recovered")
    d = _live(client)
    assert start["meeting_id"] not in [m["meeting_id"] for m in d["meetings"]]
    assert d["summary"]["recovered_stops_24h"] == 1


def test_a_start_older_than_the_window_is_counted_not_shown(client, tmp_db_path):
    old = _start(client, tmp_db_path, minutes_ago=10 * 60)
    d = _live(client)
    assert old["meeting_id"] not in [m["meeting_id"] for m in d["meetings"]]
    assert d["summary"]["unfinished_beyond_window_7d"] == 1
    assert _live(client, window_hours=12)["meetings"][0]["meeting_id"] == old["meeting_id"]


def test_the_orphan_half_of_a_resumed_meeting_is_hidden(client, tmp_db_path):
    first = _start(client, tmp_db_path, minutes_ago=30)
    second = _start(client, tmp_db_path, minutes_ago=3, device_id=first["device_id"],
                    resumed_from_meeting_id=first["meeting_id"])
    d = _live(client)
    ids = [m["meeting_id"] for m in d["meetings"]]
    assert ids == [second["meeting_id"]]
    assert d["meetings"][0]["resumed_from_meeting_id"] == first["meeting_id"]
    assert d["summary"]["resumed"] == 1 and d["summary"]["in_progress"] == 1


def test_the_app_filter_keeps_another_apps_meeting_out(client, tmp_db_path):
    ours = _start(client, tmp_db_path, minutes_ago=1)
    theirs = _start(client, tmp_db_path, minutes_ago=1, headers={"X-App-ID": "techrehearsal"})
    ids = [m["meeting_id"] for m in _live(client, app="shouldersurf")["meetings"]]
    assert ours["meeting_id"] in ids and theirs["meeting_id"] not in ids


def test_summary_counts_add_up(client, tmp_db_path):
    a = _start(client, tmp_db_path, minutes_ago=5); _beat(client, tmp_db_path, a, duration_seconds=300)
    b = _start(client, tmp_db_path, minutes_ago=5); _beat(client, tmp_db_path, b, duration_seconds=300, paused=True)
    _start(client, tmp_db_path, minutes_ago=1)
    _start(client, tmp_db_path, minutes_ago=50)
    s = _live(client)["summary"]
    assert (s["live"], s["paused"], s["early"], s["quiet"], s["in_progress"]) == (1, 1, 1, 1, 4)
