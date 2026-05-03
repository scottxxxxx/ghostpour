"""Pin the 90-day retention prune for email_events.

The prune runs at every container start (via init_db). Suppression
rows are NOT pruned — they're kept indefinitely so a suppressed
address can't slip back into rotation just because the suppression
event is old.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.database import init_db


def _seed_event(
    db_path: Path, *, event_id: str, days_ago: int, event_type: str = "email.delivered",
) -> None:
    received_at = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO email_events (id, event_type, recipient, email_id, bounce_type, payload, received_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, event_type, "u@example.com", None, None, json.dumps({}), received_at),
    )
    conn.commit()
    conn.close()


def _seed_suppression(db_path: Path, *, recipient: str, days_ago: int) -> None:
    suppressed_at = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO email_suppression (recipient, reason, source_event_id, suppressed_at)"
        " VALUES (?, ?, ?, ?)",
        (recipient.lower(), "hard_bounce", "msg_test", suppressed_at),
    )
    conn.commit()
    conn.close()


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


@pytest.fixture
def fresh_db(tmp_path):
    """Initialize a fresh DB at a tmp path so we can re-init it later
    in the test (to trigger the prune) without interfering with the
    `client` fixture's own DB lifecycle."""
    db_path = tmp_path / "retention.db"
    asyncio.run(init_db(f"sqlite+aiosqlite:///{db_path}"))
    return db_path


def test_prune_drops_email_events_older_than_90_days(fresh_db):
    _seed_event(fresh_db, event_id="old_1", days_ago=120)
    _seed_event(fresh_db, event_id="old_2", days_ago=91)
    _seed_event(fresh_db, event_id="recent_1", days_ago=89)
    _seed_event(fresh_db, event_id="recent_2", days_ago=1)

    assert _count(fresh_db, "email_events") == 4

    # Re-init the DB → triggers the prune
    asyncio.run(init_db(f"sqlite+aiosqlite:///{fresh_db}"))

    assert _count(fresh_db, "email_events") == 2

    conn = sqlite3.connect(fresh_db)
    surviving = {row[0] for row in conn.execute("SELECT id FROM email_events").fetchall()}
    conn.close()
    assert surviving == {"recent_1", "recent_2"}


def test_prune_does_not_touch_email_suppression(fresh_db):
    """Suppression list rows are kept indefinitely — even one inserted
    a year ago. Pin that explicitly so a future maintainer doesn't add
    a parallel prune for the wrong table."""
    _seed_suppression(fresh_db, recipient="ancient@example.com", days_ago=400)
    _seed_suppression(fresh_db, recipient="recent@example.com", days_ago=1)

    assert _count(fresh_db, "email_suppression") == 2

    asyncio.run(init_db(f"sqlite+aiosqlite:///{fresh_db}"))

    assert _count(fresh_db, "email_suppression") == 2


def test_prune_runs_idempotently(fresh_db):
    """Running init_db twice in a row shouldn't re-purge anything
    (no new rows to drop) and shouldn't error."""
    _seed_event(fresh_db, event_id="recent", days_ago=1)
    asyncio.run(init_db(f"sqlite+aiosqlite:///{fresh_db}"))
    asyncio.run(init_db(f"sqlite+aiosqlite:///{fresh_db}"))
    assert _count(fresh_db, "email_events") == 1
