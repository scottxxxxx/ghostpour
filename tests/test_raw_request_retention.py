"""usage_log raw_request / raw_response trimmed after 30 days (Scott, 2026-10-02).

The stored request carries the transcript as sent, so without this the
30 day transcript rule did not hold. The row stays (tokens, cost, timing);
only the two keys go. KEEP_RAW_REQUESTS_FOR accounts keep theirs.
"""
import json
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio

from app.database import init_db
from app.services import retention

SCOTT = "fa4d903c-24c0-45d5-9fdb-b5496e32501b"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


async def _seed(db, rid, user, days_ago):
    md = {"raw_request": '{"system": "TRANSCRIPT"}', "raw_response": '{"content": []}',
          "usage": {"input_tokens": 10}}
    await db.execute(
        "INSERT INTO usage_log (id, user_id, provider, model, request_timestamp, status, metadata)"
        " VALUES (?, ?, 'anthropic', 'm', ?, 'success', ?)",
        (rid, user, _iso(days_ago), json.dumps(md)))


async def _meta(db, rid):
    row = await (await db.execute("SELECT metadata FROM usage_log WHERE id = ?", (rid,))).fetchone()
    return json.loads(row[0])


@pytest_asyncio.fixture
async def db(tmp_path):
    path = str(tmp_path / "t.db")
    await init_db(f"sqlite+aiosqlite:///{path}")
    async with aiosqlite.connect(path) as conn:
        for uid in ("someone", SCOTT):
            await conn.execute(
                "INSERT INTO users (id, apple_sub, tier, created_at, updated_at) VALUES (?, ?, 'free', ?, ?)",
                (uid, "sub-" + uid, _iso(100), _iso(100)))
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_old_rows_lose_the_text_and_keep_everything_else(db):
    await _seed(db, "old", "someone", 31)
    await _seed(db, "new", "someone", 29)
    await db.commit()
    changed = await retention.strip_old_raw_requests(db)
    assert changed == 1
    old, new = await _meta(db, "old"), await _meta(db, "new")
    assert "raw_request" not in old and "raw_response" not in old
    assert old["usage"] == {"input_tokens": 10}  # the row's numbers stay
    assert new["raw_request"] == '{"system": "TRANSCRIPT"}'


@pytest.mark.asyncio
async def test_scotts_account_keeps_its_requests_forever(db):
    await _seed(db, "scott-old", SCOTT, 400)
    await db.commit()
    assert await retention.strip_old_raw_requests(db) == 0
    assert "raw_request" in await _meta(db, "scott-old")


@pytest.mark.asyncio
async def test_rerunning_changes_nothing(db):
    await _seed(db, "old", "someone", 31)
    await db.commit()
    assert await retention.strip_old_raw_requests(db) == 1
    assert await retention.strip_old_raw_requests(db) == 0


@pytest.mark.asyncio
async def test_the_scheduled_sweep_runs_the_strip(db):
    await _seed(db, "old", "someone", 31)
    await db.commit()
    out = await retention.purge_expired(db)
    assert out["usage_log.raw_request"] == 1
    assert "raw_request" not in await _meta(db, "old")


def test_the_window_is_the_transcript_window_and_scott_is_kept():
    assert retention.RAW_REQUEST_RETENTION_DAYS == retention.TRANSCRIPT_RETENTION_DAYS == 30
    assert retention.KEEP_RAW_REQUESTS_FOR == (SCOTT,)
