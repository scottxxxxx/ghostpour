"""The hourly retention sweep must not hold the write lock long enough to
fail a chat turn's bookkeeping, and that bookkeeping must survive a lock
(2026-10-07: at 03:13:48Z a stripped-in-one-transaction sweep held the lock
past the 5s busy timeout, and an N-400 turn 500'd after its model call)."""
import asyncio
import logging
import sqlite3

import aiosqlite
import pytest

from app.services import retention, usage_tracker
from tests.test_raw_request_retention import _meta, _seed, db  # noqa: F401  (fixture)


@pytest.mark.asyncio
async def test_the_strip_runs_every_batch(db):  # noqa: F811
    for i in range(120):
        await _seed(db, f"r{i}", "someone", 40)
    await db.commit()
    changed = await retention.strip_old_raw_requests(db, batch_size=50)
    assert changed == 120
    assert "raw_request" not in await _meta(db, "r119")
    assert await retention.strip_old_raw_requests(db, batch_size=50) == 0


@pytest.mark.asyncio
async def test_the_strip_commits_between_batches(db):  # noqa: F811
    for i in range(3):
        await _seed(db, f"c{i}", "someone", 40)
    await db.commit()
    await retention.strip_old_raw_requests(db, batch_size=1)
    assert not db.in_transaction


async def _hold_write_lock(path, seconds):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    await asyncio.sleep(seconds)
    conn.execute("COMMIT")
    conn.close()


async def _path(conn):
    return (await (await conn.execute("PRAGMA database_list")).fetchone())[2]


@pytest.mark.asyncio
async def test_a_bookkeeping_write_waits_out_a_short_lock(db, monkeypatch):  # noqa: F811
    path = await _path(db)
    monkeypatch.setattr(usage_tracker, "_LOCK_RETRY_DELAYS", (0.3, 0.3, 0.3))
    async with aiosqlite.connect(path, timeout=0.05) as w:
        holder = asyncio.create_task(_hold_write_lock(path, 0.5))
        await asyncio.sleep(0.05)
        ok = await usage_tracker._write_with_lock_retry(
            w, "UPDATE users SET monthly_used_usd = 1 WHERE id = 'someone'", (),
            what="test", user_id="someone", cost=0.01)
        await holder
    assert ok is True


@pytest.mark.asyncio
async def test_a_lock_that_outlasts_the_retries_is_logged_not_raised(db, monkeypatch, caplog):  # noqa: F811
    path = await _path(db)
    monkeypatch.setattr(usage_tracker, "_LOCK_RETRY_DELAYS", (0.05,))
    async with aiosqlite.connect(path, timeout=0.05) as w:
        holder = asyncio.create_task(_hold_write_lock(path, 1.0))
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.ERROR):
            ok = await usage_tracker._write_with_lock_retry(
                w, "UPDATE users SET monthly_used_usd = 1 WHERE id = 'someone'", (),
                what="log_usage", user_id="someone", cost=0.0123)
        await holder
    assert ok is False
    assert "usage_bookkeeping_failed what=log_usage" in caplog.text and "0.0123" in caplog.text


@pytest.mark.asyncio
async def test_a_non_lock_error_still_raises(db):  # noqa: F811
    with pytest.raises(sqlite3.OperationalError):
        await usage_tracker._write_with_lock_retry(
            db, "UPDATE no_such_table SET x = 1", (), what="t", user_id="u", cost=None)
