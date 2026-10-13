"""Demo and test accounts out of the dashboard's numbers (Scott, 2026-10-13).

The Meeting Memory demo account for shouldersurf.com screenshots is a real
Pro account, so it would count as a Pro user and its imports as usage. The
flag users.exclude_from_stats keeps it out of the dashboard's user and tier
counts, its usage and cost analytics, the meeting telemetry and the new-user
push counts, while the account keeps its tier and every entitlement.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _usage(db, uid, n):
    conn = sqlite3.connect(db)
    for _ in range(n):
        conn.execute(
            """INSERT INTO usage_log (id, user_id, provider, model, input_tokens, output_tokens,
               estimated_cost_usd, request_timestamp, response_time_ms, status, call_type, app_id)
               VALUES (?, ?, 'anthropic', 'm', 100, 50, 0.01, ?, 900, 'success', 'chat', 'shouldersurf')""",
            (str(uuid.uuid4()), uid, datetime.now(timezone.utc).isoformat()))
    conn.execute(
        """INSERT INTO telemetry_events (id, event_type, device_id, user_id, meeting_id, received_at, app_id)
           VALUES (?, 'meeting_start', ?, ?, ?, ?, 'shouldersurf')""",
        (str(uuid.uuid4()), "dev-" + uid, uid, str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _setup(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="real-pro", tier="pro", monthly_limit=50)
    _insert_user(tmp_db_path, user_id="demo-pro", tier="pro", monthly_limit=50)
    _usage(tmp_db_path, "real-pro", 2)
    _usage(tmp_db_path, "demo-pro", 3)
    r = client.post("/webhooks/admin/stats-exclusion", headers=ADMIN,
                    json={"user_id": "demo-pro", "excluded": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": "demo-pro", "tier": "pro", "exclude_from_stats": True}


def _dash(client):
    r = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_counts_skip_the_flagged_account(client, tmp_db_path):
    before = _dash(client)["users"]
    _setup(client, tmp_db_path)
    users = _dash(client)["users"]
    assert users["total"] == before["total"] + 1
    assert users["by_tier"].get("pro", 0) == before["by_tier"].get("pro", 0) + 1


def test_usage_and_cost_skip_the_flagged_account(client, tmp_db_path):
    before = _dash(client)["usage"]["total_requests"]
    _setup(client, tmp_db_path)
    assert _dash(client)["usage"]["total_requests"] == before + 2


def _meeting_starts(client):
    rich = client.get("/webhooks/admin/telemetry/rich?days=7", headers=ADMIN)
    assert rich.status_code == 200, rich.text
    return int(rich.json()["kpis"]["meeting_starts"] or 0)


def test_meeting_telemetry_skips_the_flagged_account(client, tmp_db_path):
    before = _meeting_starts(client)
    _setup(client, tmp_db_path)
    assert _meeting_starts(client) == before + 1


def test_new_user_push_counts_skip_it(client, tmp_db_path):
    import asyncio
    import aiosqlite
    from app.services.new_user_push import counts
    _setup(client, tmp_db_path)

    async def go():
        async with aiosqlite.connect(tmp_db_path) as db:
            return await counts(db)
    c = asyncio.run(go())
    conn = sqlite3.connect(tmp_db_path)
    all_active = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND tier != 'automation'").fetchone()[0]
    conn.close()
    assert c["total"] == all_active - 1


def test_the_account_keeps_its_tier_and_is_listed_with_the_flag(client, tmp_db_path):
    _setup(client, tmp_db_path)
    r = client.get("/webhooks/admin/users", headers=ADMIN)
    assert r.status_code == 200, r.text
    rows = r.json()["users"] if isinstance(r.json(), dict) else r.json()
    demo = next(u for u in rows if u["id"] == "demo-pro")
    assert demo["tier"] == "pro" and demo["exclude_from_stats"] in (1, True)


def test_unflag_puts_it_back_and_unknown_user_is_404(client, tmp_db_path):
    _setup(client, tmp_db_path)
    with_flag = _dash(client)["users"]["total"]
    r = client.post("/webhooks/admin/stats-exclusion", headers=ADMIN,
                    json={"user_id": "demo-pro", "excluded": False})
    assert r.json()["exclude_from_stats"] is False
    assert _dash(client)["users"]["total"] == with_flag + 1
    r = client.post("/webhooks/admin/stats-exclusion", headers=ADMIN,
                    json={"user_id": "nobody", "excluded": True})
    assert r.status_code == 404
