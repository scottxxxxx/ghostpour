"""Daily MRR run-rate replay (Scott 2026-07-28): same state semantics
as monthly_aggregates, sampled per day. List price by convention."""

import asyncio
import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _seed_event(db_path, user_id, event_type, to_tier, effective_at):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, to_tier, source, effective_at, recorded_at)
           VALUES (?,?,?,?,?,?,?)""",
        (uuid.uuid4().hex, user_id, event_type, to_tier, "assn",
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
