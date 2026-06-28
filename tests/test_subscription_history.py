"""Subscription history: append-only log, caches, report, targeting, reconcile."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite
import pytest

from app.models.user import UserRecord
from app.routers.promo import _targeting_matches
from app.services import subscriptions as subs

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _seed_user(db_path: str, user_id: str, tier: str = "free", email: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, tier, created_at, updated_at, is_active,
            monthly_used_usd, overage_balance_usd)
           VALUES (?,?,?,?,?,?,1,0,0)""",
        (user_id, f"sub_{user_id}", email or f"{user_id}@t.co", tier, now, now),
    )
    conn.commit()
    conn.close()


def _seed_event(db_path, user_id, event_type, to_tier, effective_at, from_tier=None,
                otid=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, from_tier, to_tier, original_transaction_id,
            source, effective_at, recorded_at)
           VALUES (?,?,?,?,?,?, 'assn', ?, ?)""",
        (str(uuid.uuid4()), user_id, event_type, from_tier, to_tier, otid,
         effective_at, effective_at),
    )
    conn.commit()
    conn.close()


def _mk_user(**over) -> UserRecord:
    base = dict(id="u1", apple_sub="s1", created_at="2026-01-01", updated_at="2026-01-01")
    base.update(over)
    return UserRecord(**base)


# --- record + caches ---------------------------------------------------------

@pytest.mark.asyncio
async def test_record_event_marks_ever_subscribed(client, tmp_db_path):
    _seed_user(tmp_db_path, "u-rec")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await subs.record_subscription_event(
            db, user_id="u-rec", event_type="subscribed", to_tier="plus",
            from_tier="free", effective_at="2026-03-01T00:00:00+00:00",
        )
        row = await (await db.execute(
            "SELECT ever_subscribed, first_subscribed_at FROM users WHERE id='u-rec'"
        )).fetchone()
        assert row["ever_subscribed"] == 1
        assert row["first_subscribed_at"] == "2026-03-01T00:00:00+00:00"
        n = await (await db.execute(
            "SELECT COUNT(*) c FROM subscription_events WHERE user_id='u-rec'"
        )).fetchone()
        assert n["c"] == 1


@pytest.mark.asyncio
async def test_downgrade_keeps_ever_subscribed(client, tmp_db_path):
    _seed_user(tmp_db_path, "u-dg")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await subs.record_subscription_event(
            db, user_id="u-dg", event_type="subscribed", to_tier="plus",
            effective_at="2026-02-01T00:00:00+00:00")
        await subs.record_subscription_event(
            db, user_id="u-dg", event_type="expired", to_tier="free", from_tier="plus",
            effective_at="2026-05-01T00:00:00+00:00")
        row = await (await db.execute(
            "SELECT ever_subscribed, first_subscribed_at FROM users WHERE id='u-dg'"
        )).fetchone()
        # ever_subscribed sticks; first_subscribed_at stays the earliest paid date
        assert row["ever_subscribed"] == 1
        assert row["first_subscribed_at"] == "2026-02-01T00:00:00+00:00"


# --- month-by-month report ---------------------------------------------------

@pytest.mark.asyncio
async def test_monthly_aggregates_replays_timeline(client, tmp_db_path):
    _seed_user(tmp_db_path, "A")
    _seed_user(tmp_db_path, "B")
    _seed_event(tmp_db_path, "A", "subscribed", "plus", "2026-01-15T00:00:00+00:00")
    _seed_event(tmp_db_path, "B", "subscribed", "plus", "2026-02-05T00:00:00+00:00")
    _seed_event(tmp_db_path, "A", "upgraded", "pro", "2026-03-10T00:00:00+00:00", from_tier="plus")
    _seed_event(tmp_db_path, "B", "expired", "free", "2026-04-20T00:00:00+00:00", from_tier="plus")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        months = {m["month"]: m for m in await subs.monthly_aggregates(db)}
    assert months["2026-01"]["active_by_tier"] == {"plus": 1}
    assert months["2026-01"]["new_subscriptions"] == 1
    assert months["2026-02"]["active_total"] == 2
    assert months["2026-03"]["active_by_tier"] == {"plus": 1, "pro": 1}
    assert months["2026-04"]["churns"] == 1
    assert months["2026-04"]["active_by_tier"] == {"pro": 1}
    # MRR for April: one pro @ 14.99 gross, net after 15%
    assert months["2026-04"]["gross_usd"] == 14.99
    assert months["2026-04"]["net_usd"] == round(14.99 * 0.85, 2)


# --- admin endpoints ---------------------------------------------------------

def test_subscriptions_report_endpoint(client, tmp_db_path):
    _seed_user(tmp_db_path, "A", tier="pro")
    _seed_event(tmp_db_path, "A", "subscribed", "pro", "2026-01-01T00:00:00+00:00")
    r = client.get("/webhooks/admin/subscriptions", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["paid_now"] == 1
    assert body["summary"]["active_by_tier"] == {"pro": 1}
    assert any(m["month"] == "2026-01" for m in body["monthly"])


def test_subscriptions_csv_export(client, tmp_db_path):
    _seed_user(tmp_db_path, "A")
    _seed_event(tmp_db_path, "A", "subscribed", "plus", "2026-01-01T00:00:00+00:00")
    r = client.get("/webhooks/admin/subscriptions/export.csv", headers=ADMIN)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert r.text.splitlines()[0].startswith("month,")


def test_user_subscription_timeline_endpoint(client, tmp_db_path):
    _seed_user(tmp_db_path, "A", email="a@t.co")
    _seed_event(tmp_db_path, "A", "subscribed", "plus", "2026-01-01T00:00:00+00:00")
    _seed_event(tmp_db_path, "A", "renewed", "plus", "2026-02-01T00:00:00+00:00")
    r = client.get("/webhooks/admin/user/A/subscription", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert len(body["timeline"]) == 2
    assert body["timeline"][0]["event_type"] == "subscribed"  # oldest first
    r404 = client.get("/webhooks/admin/user/nope/subscription", headers=ADMIN)
    assert r404.status_code == 404


def test_reconcile_dormant_without_keys(client, tmp_db_path):
    r = client.post("/webhooks/admin/subscriptions/reconcile", headers=ADMIN)
    assert r.status_code == 200
    assert r.json().get("skipped") == "not_configured"


def test_admin_key_required(client):
    assert client.get("/webhooks/admin/subscriptions", headers={"X-Admin-Key": "x"}).status_code == 403


# --- ever_subscribed marking (Apple-confirmed) -------------------------------

@pytest.mark.asyncio
async def test_mark_ever_subscribed_keeps_earliest(client, tmp_db_path):
    _seed_user(tmp_db_path, "m1")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await subs.mark_ever_subscribed(db, "m1", when="2026-05-01T00:00:00+00:00")
        await subs.mark_ever_subscribed(db, "m1", when="2026-02-01T00:00:00+00:00")  # earlier wins
        await subs.mark_ever_subscribed(db, "m1", when="2026-09-01T00:00:00+00:00")  # later ignored
        await subs.mark_ever_subscribed(db, "m1")  # undated must not clobber the date
        row = await (await db.execute(
            "SELECT ever_subscribed, first_subscribed_at FROM users WHERE id='m1'"
        )).fetchone()
        assert row["ever_subscribed"] == 1
        assert row["first_subscribed_at"] == "2026-02-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_reconcile_marks_ever_subscribed_when_in_sync(client, tmp_db_path, monkeypatch):
    from app.services import app_store_server_api as assa
    from app.services import subscription_reconcile as recon
    _seed_user(tmp_db_path, "r1", tier="pro")
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET original_transaction_id='otid-r1' WHERE id='r1'")
    conn.commit(); conn.close()

    async def fake_state(otid):
        return {"entitled": True, "status": 1, "tier": "pro", "product_id": "x",
                "expires_at": None, "original_purchase_date": "2026-01-10T00:00:00+00:00",
                "environment": "Sandbox", "original_transaction_id": otid}
    monkeypatch.setattr(assa, "is_configured", lambda: True)
    monkeypatch.setattr(assa, "get_subscription_state", fake_state)

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, tier, original_transaction_id FROM users WHERE id='r1'"
        )).fetchone()
        res = await recon.reconcile_user(db, row, None)  # pro==pro, no drift fix
        assert res is None
        u = await (await db.execute(
            "SELECT ever_subscribed, first_subscribed_at FROM users WHERE id='r1'"
        )).fetchone()
        assert u["ever_subscribed"] == 1
        assert u["first_subscribed_at"] == "2026-01-10T00:00:00+00:00"


# --- promo targeting ---------------------------------------------------------

def test_never_subscribed_targeting():
    tgt = {"subscription": {"ever_subscribed": False}}
    never = _mk_user(ever_subscribed=False)
    subbed = _mk_user(ever_subscribed=True)
    assert _targeting_matches(tgt, never, None) is True
    assert _targeting_matches(tgt, subbed, None) is False
    # anonymous = no record = treated as never-subscribed (reach)
    assert _targeting_matches(tgt, None, None) is True


def test_ever_subscribed_targeting():
    tgt = {"subscription": {"ever_subscribed": True}}
    assert _targeting_matches(tgt, _mk_user(ever_subscribed=True), None) is True
    assert _targeting_matches(tgt, _mk_user(ever_subscribed=False), None) is False
    assert _targeting_matches(tgt, None, None) is False
