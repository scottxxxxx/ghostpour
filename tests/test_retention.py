"""Pin every retention window, and pin that they are actually enforced.

The windows are Scott's calls and unchanged here. What this file guards
is the enforcement: each of these DELETEs used to live only inside
`init_db()`, so a row past its window survived until the next container
start and real retention was "N days plus however long until the next
deploy". Rows were measurably sitting past the line.

The transcript window is the one with a user-visible consequence: the
phone and CQ hold the durable copies, and a report requested for a
purged meeting comes back 404 `no_meeting_data`, which is the client's
cue to re-send the capture and retry.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database import init_db
from app.services.retention import SWEEPS, purge_expired


def _stamp(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _seed_transcript(db_path, *, meeting_id: str, days_ago: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO meeting_transcripts"
        " (id, user_id, meeting_id, transcript, project, project_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "u-ret", meeting_id, "Speaker: hello world",
         "Proj", "p-1", _stamp(days_ago)),
    )
    conn.commit()
    conn.close()


def _seed_snapshot(db_path, *, snap_id: str, days_ago: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO plan_snapshots"
        " (id, user_id, app_id, project_id, template_id, project_name,"
        "  meeting_date, tasks_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (snap_id, "u-ret", "shouldersurf", "p-1", "gantt_detailed", "Proj",
         None, "[]", _stamp(days_ago)),
    )
    conn.commit()
    conn.close()


def _seed_telemetry(db_path, *, device_id: str, days_ago: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO telemetry_events (id, device_id, event_type, received_at)"
        " VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), device_id, "ping", _stamp(days_ago)),
    )
    conn.commit()
    conn.close()


def _fresh_db(tmp_path, name: str) -> str:
    db_path = str(tmp_path / name)
    asyncio.run(init_db(f"sqlite+aiosqlite:///{db_path}"))
    return db_path


def _sweep(db_path: str) -> dict[str, int]:
    import aiosqlite

    async def _run():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            return await purge_expired(db)

    return asyncio.run(_run())


def test_plan_snapshots_purge_at_365_days(tmp_path):
    db_path = _fresh_db(tmp_path, "snap.db")
    _seed_snapshot(db_path, snap_id="s-old", days_ago=366)
    _seed_snapshot(db_path, snap_id="s-edge", days_ago=360)
    _seed_snapshot(db_path, snap_id="s-new", days_ago=1)

    asyncio.run(init_db(f"sqlite+aiosqlite:///{db_path}"))

    conn = sqlite3.connect(db_path)
    kept = {r[0] for r in conn.execute("SELECT id FROM plan_snapshots").fetchall()}
    conn.close()
    assert kept == {"s-edge", "s-new"}


def test_transcripts_purge_at_30_days(tmp_path):
    db_path = _fresh_db(tmp_path, "ret.db")
    _seed_transcript(db_path, meeting_id="m-old", days_ago=31)
    _seed_transcript(db_path, meeting_id="m-edge", days_ago=29)
    _seed_transcript(db_path, meeting_id="m-new", days_ago=0)

    asyncio.run(init_db(f"sqlite+aiosqlite:///{db_path}"))  # boot-time purge

    conn = sqlite3.connect(db_path)
    kept = {r[0] for r in conn.execute(
        "SELECT meeting_id FROM meeting_transcripts").fetchall()}
    conn.close()
    assert kept == {"m-edge", "m-new"}


def test_the_sweep_purges_without_a_restart(tmp_path):
    """The point of the scheduled sweep: rows that cross the line while
    the process is UP get dropped without waiting for a deploy. Boot is
    deliberately NOT re-run here."""
    db_path = _fresh_db(tmp_path, "sweep.db")
    _seed_transcript(db_path, meeting_id="m-old", days_ago=31)
    _seed_transcript(db_path, meeting_id="m-edge", days_ago=29)
    _seed_snapshot(db_path, snap_id="s-old", days_ago=400)
    _seed_snapshot(db_path, snap_id="s-new", days_ago=10)

    deleted = _sweep(db_path)
    assert deleted["meeting_transcripts"] == 1
    assert deleted["plan_snapshots"] == 1

    conn = sqlite3.connect(db_path)
    assert {r[0] for r in conn.execute(
        "SELECT meeting_id FROM meeting_transcripts")} == {"m-edge"}
    assert {r[0] for r in conn.execute("SELECT id FROM plan_snapshots")} == {"s-new"}
    conn.close()

    # Idempotent: a second pass with nothing expired deletes nothing.
    assert set(_sweep(db_path).values()) == {0}


def test_a_device_keeps_its_first_seen_date_when_its_events_age_out(tmp_path):
    """The sharp edge in widening the sweep. init_db absorbed device
    first-seen BEFORE purging telemetry, so a sweep that skipped the
    absorb would lose the true first_seen_at for any device that first
    appeared and then aged out between two deploys — silently, and
    unrecoverably."""
    db_path = _fresh_db(tmp_path, "tel.db")
    _seed_telemetry(db_path, device_id="dev-1", days_ago=40)
    _seed_telemetry(db_path, device_id="dev-1", days_ago=35)

    deleted = _sweep(db_path)
    assert deleted["telemetry_events"] == 2

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT device_id, first_seen_at FROM telemetry_devices").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["dev-1"]
    # The EARLIEST of the two, not the survivor of the purge (there is none).
    assert rows[0][1].startswith(_stamp(40)[:10])


def test_one_bad_table_does_not_sink_the_others(tmp_path):
    """A missing table on an older schema, or a lock, should cost that
    sweep and nothing else."""
    db_path = _fresh_db(tmp_path, "partial.db")
    _seed_transcript(db_path, meeting_id="m-old", days_ago=31)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE email_events")
    conn.commit()
    conn.close()

    deleted = _sweep(db_path)
    assert deleted["email_events"] == 0        # failed, reported as zero
    assert deleted["meeting_transcripts"] == 1  # still ran


@pytest.mark.parametrize("sweep", SWEEPS, ids=lambda s: s.table)
def test_every_window_is_declared_with_its_reason(sweep):
    """A number with no reason beside it is a number nobody can revisit."""
    assert sweep.days > 0
    assert sweep.column in ("created_at", "received_at")
    assert len(sweep.why) > 40, sweep.table


def test_the_windows_are_unchanged():
    """This change moved enforcement, not policy. Any edit to a number
    here is a retention decision and belongs in its own conversation."""
    assert {s.table: s.days for s in SWEEPS} == {
        "meeting_transcripts": 30,
        "meeting_reports": 30,
        "plan_snapshots": 365,
        "email_events": 90,
        "telemetry_events": 30,
    }


def test_the_loop_is_wired():
    """A sweep that is never scheduled fails silently."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    assert "_retention_sweep_loop" in text
    assert "create_task(_retention_sweep_loop())" in text
    assert "_retention_task.cancel()" in text


def test_no_ttl_delete_is_left_behind_in_init_db():
    """The defect was a DELETE that only ever ran at startup. Catch the
    next one at review time rather than in a year of drift."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "database.py").read_text()
    assert "datetime('now', '-" not in src
