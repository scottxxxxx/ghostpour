"""The reconcile sweep must not take a tier that a NEWER purchase granted.

Scott, 2026-10-21: "My ipad keeps putting me on the free tier, but I am
purchasing the pro in the SS app." His row held an August Production
original_transaction_id that had lapsed. Each new purchase granted Pro but
never replaced that id (an unverified receipt keeps the old one by design,
and a token-matched Sandbox RESUBSCRIBE never wrote its own), so the sweep
asked Apple about the August subscription, heard "lapsed", and dropped him
to free: 21:16Z bought, 21:38Z free; 22:42Z bought, 23:33Z free; a real
Sandbox RESUBSCRIBE at 03:08Z, free at 03:57Z.

Two halves, both held here:
  1. the sweep skips a downgrade when a grant under a DIFFERENT id came
     after the stored id lapsed (a lapse of the SAME id is still fixed);
  2. a SUBSCRIBED/DID_RENEW matched only by appAccountToken adopts Apple's
     id, so the sweep asks about the right subscription afterwards.
"""

from __future__ import annotations

import sqlite3
import uuid

import aiosqlite
import pytest

from tests.conftest import _insert_user

OLD_OTID = "120003884095739"       # the shape on Scott's row: lapsed 08-28
OLD_EXPIRES = "2026-08-28T02:23:17+00:00"
NEW_OTID = "2000001242752401"      # the 10-21 Sandbox RESUBSCRIBE's id
PRO = "com.weirtech.shouldersurf.sub.pro.monthly"


def _set(db_path, sql, args):
    conn = sqlite3.connect(db_path)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _one(db_path, sql, args):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _event(db_path, user_id, event_type, to_tier, effective_at, otid=None,
           environment="Unverified", source="verify_receipt"):
    _set(db_path,
         """INSERT INTO subscription_events
            (id, user_id, event_type, from_tier, to_tier, original_transaction_id,
             source, environment, effective_at, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
         (str(uuid.uuid4()), user_id, event_type, "free", to_tier, otid,
          source, environment, effective_at, effective_at))


@pytest.fixture
def lapsed_apple(monkeypatch):
    from app.services import app_store_server_api as assa
    asked = []

    async def fake_state(otid):
        asked.append(otid)
        return {"entitled": False, "status": 2, "tier": "pro", "product_id": PRO,
                "expires_at": OLD_EXPIRES,
                "original_purchase_date": "2026-07-28T02:23:17+00:00",
                "environment": "Production", "original_transaction_id": otid}
    monkeypatch.setattr(assa, "is_configured", lambda: True)
    monkeypatch.setattr(assa, "get_subscription_state", fake_state)
    return asked


async def _reconcile(db_path, user_id):
    from app.config import get_settings
    from app.models.tier import load_tier_config
    from app.services import subscription_reconcile as recon
    tc = load_tier_config(get_settings().tier_config_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, tier, original_transaction_id FROM users WHERE id = ?",
            (user_id,))).fetchone()
        res = await recon.reconcile_user(db, row, tc)
        await db.commit()
        return res


def _pro_with_old_id(db_path, user_id):
    _insert_user(db_path, user_id=user_id, tier="pro")
    _set(db_path, "UPDATE users SET original_transaction_id = ? WHERE id = ?",
         (OLD_OTID, user_id))


@pytest.mark.asyncio
async def test_a_newer_unverified_purchase_is_not_reconciled_away(
        client, tmp_db_path, lapsed_apple):
    """Scott's exact history: the grant carries no id at all."""
    u = "fa4d903c-0000-4000-8000-000000000001"
    _pro_with_old_id(tmp_db_path, u)
    _event(tmp_db_path, u, "subscribed", "pro", "2026-10-21T22:42:05.403030+00:00")

    assert await _reconcile(tmp_db_path, u) is None
    assert lapsed_apple == [OLD_OTID]
    assert _one(tmp_db_path, "SELECT tier FROM users WHERE id = ?", (u,))[0] == "pro"


@pytest.mark.asyncio
async def test_a_newer_purchase_under_another_id_is_not_reconciled_away(
        client, tmp_db_path, lapsed_apple):
    """The 03:08Z Sandbox RESUBSCRIBE: its own id, never written to the row."""
    u = "fa4d903c-0000-4000-8000-000000000002"
    _pro_with_old_id(tmp_db_path, u)
    _event(tmp_db_path, u, "subscribed", "pro", "2026-10-21T03:08:43.538626+00:00",
           otid=NEW_OTID, environment="Sandbox", source="assn")

    assert await _reconcile(tmp_db_path, u) is None
    assert _one(tmp_db_path, "SELECT tier FROM users WHERE id = ?", (u,))[0] == "pro"


@pytest.mark.asyncio
async def test_a_lapse_of_the_same_subscription_is_still_fixed(
        client, tmp_db_path, lapsed_apple):
    """The sweep's whole job. A renewal of the stored id after its own
    expiry date must not shield it: same id, so the lapse is real drift."""
    u = "fa4d903c-0000-4000-8000-000000000003"
    _pro_with_old_id(tmp_db_path, u)
    _event(tmp_db_path, u, "renewed", "pro", "2026-09-01T00:00:00+00:00",
           otid=OLD_OTID, environment="Production", source="assn")

    assert await _reconcile(tmp_db_path, u) == "pro->free"
    assert _one(tmp_db_path, "SELECT tier FROM users WHERE id = ?", (u,))[0] == "free"


@pytest.mark.asyncio
async def test_a_grant_from_before_the_lapse_does_not_shield_it(
        client, tmp_db_path, lapsed_apple):
    u = "fa4d903c-0000-4000-8000-000000000004"
    _pro_with_old_id(tmp_db_path, u)
    _event(tmp_db_path, u, "subscribed", "pro", "2026-08-01T00:00:00+00:00")

    assert await _reconcile(tmp_db_path, u) == "pro->free"


@pytest.mark.asyncio
async def test_a_grant_of_a_different_tier_does_not_shield_it(
        client, tmp_db_path, lapsed_apple):
    u = "fa4d903c-0000-4000-8000-000000000005"
    _pro_with_old_id(tmp_db_path, u)
    _event(tmp_db_path, u, "subscribed", "plus", "2026-10-21T22:42:05+00:00")

    assert await _reconcile(tmp_db_path, u) == "pro->free"


# --- half 2: a token-matched upgrade adopts Apple's id ---------------------

def _post(client, monkeypatch, token, notification_type="SUBSCRIBED", otid=NEW_OTID):
    from app.routers import apple_webhooks
    monkeypatch.setattr(
        apple_webhooks, "decode_notification",
        lambda payload, bid: {
            "notificationType": notification_type,
            "subtype": "RESUBSCRIBE",
            "data": {
                "bundleId": "com.test.app",
                "environment": "Sandbox",
                "signedTransactionInfo": {
                    "originalTransactionId": otid,
                    "transactionId": "2000001254717993",
                    "productId": PRO,
                    "appAccountToken": token,
                },
            },
        })
    return client.post("/v1/apple-notifications", json={"signedPayload": "j.w.s"})


@pytest.mark.parametrize("notification_type", ["SUBSCRIBED", "DID_RENEW"])
def test_a_token_matched_upgrade_adopts_apples_id(
        client, monkeypatch, tmp_db_path, notification_type):
    u = "5f0c9a52-8d7e-4b3a-9c1e-2a6b4d8e0f21"
    _insert_user(tmp_db_path, user_id=u, tier="free")
    _set(tmp_db_path, "UPDATE users SET original_transaction_id = ? WHERE id = ?",
         (OLD_OTID, u))

    r = _post(client, monkeypatch, u.upper(), notification_type)

    assert r.status_code == 200, r.text
    assert _one(tmp_db_path, "SELECT original_transaction_id FROM users WHERE id = ?",
                (u,))[0] == NEW_OTID


def test_an_expiry_matched_by_token_does_not_adopt_its_id(
        client, monkeypatch, tmp_db_path):
    """Only an Apple ACTIVE signal moves the id. An EXPIRED for some other
    subscription must not repoint the row at a dead one."""
    u = "5f0c9a52-8d7e-4b3a-9c1e-2a6b4d8e0f22"
    _insert_user(tmp_db_path, user_id=u, tier="pro")
    _set(tmp_db_path, "UPDATE users SET original_transaction_id = ? WHERE id = ?",
         (OLD_OTID, u))

    r = _post(client, monkeypatch, u, "EXPIRED")

    assert r.status_code == 200, r.text
    assert _one(tmp_db_path, "SELECT original_transaction_id FROM users WHERE id = ?",
                (u,))[0] == OLD_OTID
