"""Daily MRR run-rate replay (Scott 2026-07-28): same state semantics
as monthly_aggregates, sampled per day. List price by convention."""

import asyncio
import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _seed_event(db_path, user_id, event_type, to_tier, effective_at,
                environment="Production"):
    """Defaults to Production: the MRR series counts Production only, so a
    Sandbox event is deliberately absent from the money line."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, to_tier, source, environment,
            effective_at, recorded_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (uuid.uuid4().hex, user_id, event_type, to_tier, "assn", environment,
         effective_at, effective_at))
    conn.commit()
    conn.close()


def test_mrr_trend_replays_daily(client, tmp_db_path):
    from app.services import subscriptions as subs

    _insert_user(tmp_db_path, "m1")
    _insert_user(tmp_db_path, "m2")
    today = datetime.now(timezone.utc)
    y = today.year
    # keep dates in the current year but firmly in the past
    _seed_event(tmp_db_path, "m1", "subscribed", "plus", f"{y}-01-01T10:00:00+00:00")
    _seed_event(tmp_db_path, "m2", "subscribed", "pro", f"{y}-01-03T10:00:00+00:00")
    _seed_event(tmp_db_path, "m1", "expired", "free", f"{y}-01-05T10:00:00+00:00")

    async def run():
        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        try:
            return await subs.mrr_trend(db)
        finally:
            await db.close()
    trend = asyncio.run(run())
    by_day = {t["day"]: t for t in trend}
    assert by_day[f"{y}-01-01"]["mrr_gross_usd"] == 9.99
    assert by_day[f"{y}-01-02"]["mrr_gross_usd"] == 9.99      # carried forward
    assert by_day[f"{y}-01-03"]["mrr_gross_usd"] == 24.98     # plus + pro
    assert by_day[f"{y}-01-05"]["mrr_gross_usd"] == 14.99     # plus expired
    assert by_day[f"{y}-01-05"]["active_paid"] == 1
    assert trend[-1]["day"] == today.date().isoformat()       # runs to today

    # rides the report endpoint
    r = client.get("/webhooks/admin/subscriptions", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["mrr_trend"][0]["day"] == f"{y}-01-01"


# --- Sandbox split (2026-08-02) --------------------------------------
#
# Three of five paid accounts were TestFlight, so MRR read $104.92 when
# real revenue was a fraction of it. Money figures are Production only;
# Sandbox rides alongside as its own series so a test cohort stays
# visible without ever being counted as revenue.


def test_sandbox_excluded_from_mrr_and_counted_separately(client, tmp_db_path):
    from app.services import subscriptions as subs

    _insert_user(tmp_db_path, "prod-sub", tier="pro")
    _insert_user(tmp_db_path, "tf-sub", tier="pro")
    _seed_event(tmp_db_path, "prod-sub", "subscribed", "pro",
                "2026-08-01T00:00:00+00:00", environment="Production")
    _seed_event(tmp_db_path, "tf-sub", "subscribed", "pro",
                "2026-08-01T00:00:00+00:00", environment="Sandbox")

    async def _run():
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            return await subs.mrr_trend(db)

    trend = asyncio.run(_run())
    last = trend[-1]
    assert last["active_paid"] == 1              # production only
    assert last["mrr_gross_usd"] == 14.99
    assert last["active_paid_sandbox"] == 1      # visible, not counted
    assert last["mrr_gross_sandbox_usd"] == 14.99


def test_summary_splits_paid_counts_by_environment(client, tmp_db_path):
    from app.services import subscriptions as subs

    _insert_user(tmp_db_path, "s-prod", tier="pro")
    _insert_user(tmp_db_path, "s-tf", tier="pro")
    _insert_user(tmp_db_path, "s-granted", tier="plus")   # no events at all
    _seed_event(tmp_db_path, "s-prod", "subscribed", "pro",
                "2026-08-01T00:00:00+00:00", environment="Production")
    _seed_event(tmp_db_path, "s-tf", "subscribed", "pro",
                "2026-08-01T00:00:00+00:00", environment="Sandbox")

    async def _run():
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            return await subs.summary(db)

    s = asyncio.run(_run())
    assert s["paid_by_env"] == {"production": 1, "sandbox": 1, "unknown": 1}
    assert s["paid_now"] == 1                    # headline is production
    assert s["paid_now_all_envs"] == 3
    assert s["current_mrr_gross_usd"] == 14.99   # pro only, not the other two
    assert s["sandbox_mrr_gross_usd"] == 14.99
    assert s["unknown_mrr_gross_usd"] == 9.99    # the granted plus
    assert s["active_by_tier"] == {"pro": 1}


def test_latest_environment_wins_per_user(client, tmp_db_path):
    """A tester who later buys for real should stop being classified as
    TestFlight, so classification follows the most recent event."""
    from app.services import subscriptions as subs

    _insert_user(tmp_db_path, "switcher", tier="pro")
    _seed_event(tmp_db_path, "switcher", "subscribed", "pro",
                "2026-07-01T00:00:00+00:00", environment="Sandbox")
    _seed_event(tmp_db_path, "switcher", "subscribed", "pro",
                "2026-08-01T00:00:00+00:00", environment="Production")

    async def _run():
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            return await subs.user_environments(db), await subs.summary(db)

    envs, s = asyncio.run(_run())
    assert envs["switcher"] == "Production"
    assert s["paid_by_env"]["production"] == 1
