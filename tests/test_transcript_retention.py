"""Pin the 30-day retention prune for meeting_transcripts (Scott,
2026-07-21). Runs at every container start via init_db, same window as
the meeting_reports purge it aligns with. The phone and CQ hold the
durable copies; GP's transcript exists for report generation,
regeneration insurance, and cleanup debugging."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from app.database import init_db


def _seed_transcript(db_path, *, meeting_id: str, days_ago: int) -> None:
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO meeting_transcripts"
        " (id, user_id, meeting_id, transcript, project, project_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "u-ret", meeting_id, "Speaker: hello world",
         "Proj", "p-1", created_at),
    )
    conn.commit()
    conn.close()


def test_transcripts_purge_at_30_days(tmp_path):
    db_path = str(tmp_path / "ret.db")
    url = f"sqlite+aiosqlite:///{db_path}"
    asyncio.run(init_db(url))
    _seed_transcript(db_path, meeting_id="m-old", days_ago=31)
    _seed_transcript(db_path, meeting_id="m-edge", days_ago=29)
    _seed_transcript(db_path, meeting_id="m-new", days_ago=0)

    asyncio.run(init_db(url))  # boot-time purge

    conn = sqlite3.connect(db_path)
    kept = {r[0] for r in conn.execute(
        "SELECT meeting_id FROM meeting_transcripts").fetchall()}
    conn.close()
    assert kept == {"m-edge", "m-new"}
