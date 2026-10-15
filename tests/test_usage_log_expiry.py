"""usage_log rows for the anonymous form apps expire after 90 days (Scott,
2026-10-14). The row is deleted, and an anonymous account's expiring spend is
carried first, so the lifetime allowance cannot be reset by waiting."""
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio

from app.database import init_db
from app.models.user import ANONYMOUS_SUB_PREFIX
from app.services import retention
from app.services.anonymous_budget import CARRIED_SQL, LIFETIME_SPEND_SQL

SCOTT = "fa4d903c-24c0-45d5-9fdb-b5496e32501b"
ANON = "anon-user"
SIGNED_IN = "apple-user"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


async def _row(db, rid, user, app, days_ago, cost):
    await db.execute(
        "INSERT INTO usage_log (id, user_id, provider, model, request_timestamp, status,"
        " app_id, case_id, estimated_cost_usd) VALUES (?, ?, 'anthropic', 'm', ?, 'success', ?, 'case1', ?)",
        (rid, user, _iso(days_ago), app, cost))


async def _ids(db):
    return {r[0] for r in await (await db.execute("SELECT id FROM usage_log")).fetchall()}


async def _install_spend(db, user, app):
    live = (await (await db.execute(LIFETIME_SPEND_SQL, (user, app))).fetchone())[0] or 0.0
    carried = await (await db.execute(CARRIED_SQL, (user, app))).fetchone()
    return round(float(live) + float(carried[0] if carried else 0.0), 6)


@pytest_asyncio.fixture
async def db(tmp_path):
    path = str(tmp_path / "t.db")
    await init_db(f"sqlite+aiosqlite:///{path}")
    async with aiosqlite.connect(path) as conn:
        for uid, sub in ((ANON, ANONYMOUS_SUB_PREFIX + "ab12"), (SIGNED_IN, "001234.apple"),
                         (SCOTT, "sub-scott")):
            await conn.execute(
                "INSERT INTO users (id, apple_sub, tier, created_at, updated_at) VALUES (?, ?, 'free', ?, ?)",
                (uid, sub, _iso(200), _iso(200)))
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_old_rows_go_and_the_lifetime_spend_does_not_move(db):
    await _row(db, "old1", ANON, "n400", 91, 1.0)
    await _row(db, "old2", ANON, "n400", 120, 0.25)
    await _row(db, "new", ANON, "n400", 10, 0.5)
    await db.commit()
    before = await _install_spend(db, ANON, "n400")
    assert await retention.expire_old_usage_rows(db) == 2
    assert await _ids(db) == {"new"}
    assert await _install_spend(db, ANON, "n400") == before == 1.75


@pytest.mark.asyncio
async def test_rerunning_neither_deletes_nor_carries_again(db):
    await _row(db, "old", ANON, "i765", 95, 1.0)
    await db.commit()
    assert await retention.expire_old_usage_rows(db) == 1
    assert await retention.expire_old_usage_rows(db) == 0
    assert await _install_spend(db, ANON, "i765") == 1.0


@pytest.mark.asyncio
async def test_a_signed_in_account_loses_the_rows_and_gets_no_carry(db):
    await _row(db, "old", SIGNED_IN, "n400", 95, 2.0)
    await db.commit()
    assert await retention.expire_old_usage_rows(db) == 1
    n = (await (await db.execute("SELECT COUNT(*) FROM anonymous_spend_carry")).fetchone())[0]
    assert n == 0


@pytest.mark.asyncio
async def test_scotts_rows_and_other_apps_rows_stay(db):
    await _row(db, "scott", SCOTT, "n400", 400, 1.0)
    await _row(db, "ss", ANON, "shouldersurf", 400, 1.0)
    await _row(db, "boundary", ANON, "n400", 89, 1.0)
    await db.commit()
    assert await retention.expire_old_usage_rows(db) == 0
    assert await _ids(db) == {"scott", "ss", "boundary"}


@pytest.mark.asyncio
async def test_the_scheduled_sweep_runs_it(db):
    await _row(db, "old", ANON, "n400", 91, 1.0)
    await db.commit()
    out = await retention.purge_expired(db)
    assert out["usage_log"] == 1 and await _ids(db) == set()
