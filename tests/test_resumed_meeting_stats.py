"""A resumed meeting counts once (Scott, 2026-10-13).

SS liveness test 3: a meeting swiped away at 131 s and resumed into a new
meeting whose stop carries the whole recording, 143 s. Both stops landed in
telemetry, so the rollups and the telemetry page counted two meetings
averaging 137 s for one meeting of 143 s. The orphan half (the meeting another
start names in resumed_from_meeting_id) is now left out of meeting counts and
durations; app starts are untouched.
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid

import aiosqlite

ADMIN = {"X-Admin-Key": "test-admin-key"}
DAY = "2026-10-13"


def _ev(conn, event_type, meeting_id=None, duration=None, resumed_from=None, t="20:55:00"):
    conn.execute(
        """INSERT INTO telemetry_events (id, event_type, device_id, user_id, meeting_id,
           duration_seconds, received_at, app_id, model_id, resumed_from_meeting_id, stop_reason)
           VALUES (?, ?, 'dev-1', NULL, ?, ?, ?, 'shouldersurf', 'cloudzap/auto', ?, ?)""",
        (str(uuid.uuid4()), event_type, meeting_id, duration, f"{DAY}T{t}+00:00", resumed_from,
         None if event_type != "meeting_stop" else "user"))


def _seed(path):
    conn = sqlite3.connect(path)
    _ev(conn, "meeting_start", "A", t="20:52:47")
    _ev(conn, "meeting_stop", "A", 131, t="20:55:29")
    _ev(conn, "app_start", t="20:55:29")
    _ev(conn, "meeting_start", "B", resumed_from="A", t="20:55:31")
    _ev(conn, "meeting_stop", "B", 143, t="20:55:43")
    _ev(conn, "meeting_start", "C", t="21:00:00")
    _ev(conn, "meeting_stop", "C", 60, t="21:01:00")
    conn.commit()
    conn.close()


def _rollup(path):
    from app.services.telemetry_rollup import _compute_day

    async def go():
        async with aiosqlite.connect(path) as db:
            await _compute_day(db, DAY)
            await db.commit()
            cur = await db.execute("SELECT metric, value FROM telemetry_daily_rollups WHERE day = ?", (DAY,))
            return {m: v for m, v in await cur.fetchall()}
    return asyncio.run(go())


def test_rollup_counts_the_resumed_pair_once(client, tmp_db_path):
    _seed(tmp_db_path)
    r = _rollup(tmp_db_path)
    assert r["meetings_started"] == 2 and r["meetings_stopped"] == 2
    assert r["app_starts"] == 1
    assert r["duration_avg_sec"] == (143 + 60) / 2
    assert r["duration_min_sec"] == 60 and r["duration_max_sec"] == 143


def test_rollup_per_model_counts_skip_the_orphan(client, tmp_db_path):
    _seed(tmp_db_path)
    r = _rollup(tmp_db_path)
    assert r["meetings_per_model:cloudzap/auto"] == 4


def test_telemetry_page_counts_the_resumed_pair_once(client, tmp_db_path):
    _seed(tmp_db_path)
    r = client.get("/webhooks/admin/telemetry/rich?days=7", headers=ADMIN)
    assert r.status_code == 200, r.text
    k = r.json()["kpis"]
    assert int(k["meeting_starts"]) == 2 and int(k["meeting_stops"]) == 2
    assert round(k["avg_duration_sec"]) == round((143 + 60) / 2)
    assert int(k["app_starts"]) == 1
