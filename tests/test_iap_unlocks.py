"""One-time IAP unlocks (Scott, 2026-10-13; spec SS docs/MODEL_UNLOCKS_IAP.md).

Two packs, unlock_apple_cloud and unlock_byok, are per-USER features, not
tiers. They live in `purchases`, never in users.tier or the one subscription
slot. Scott's rulings: the $4.99 "complete" pack grants the missing pack;
with zero packs it waits as pending, with both it grants nothing and is
flagged for refund. A refund of an unlock revokes the unlock and NOTHING
else: the bug this build fixed first was a webhook that downgraded any
refund to free, so a refunded unlock would have stripped a subscriber.
Free Project Chat on new builds: SS AI gets one conversation a month,
signed-in only; Apple Cloud or own key needs the matching pack; on-device
gets only the switch to SS AI. Old builds keep today's behaviour.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from tests.conftest import _insert_user, _jwt_token

BUNDLE = "com.test.app"
APPLE = "com.weirtech.shouldersurf.unlock.applecloud"
BYOK = "com.weirtech.shouldersurf.unlock.byok"
BOTH = "com.weirtech.shouldersurf.unlock.bundle"
COMPLETE = "com.weirtech.shouldersurf.unlock.complete"
PRO_SUB = "com.weirtech.shouldersurf.sub.pro.monthly"


def _user(db, uid=None, tier="free", anonymous=False):
    uid = uid or "u-" + uuid.uuid4().hex[:8]
    _insert_user(db, user_id=uid, tier=tier, monthly_limit=2.0)
    if anonymous:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET apple_sub = ? WHERE id = ?", (f"anonymous:{uid}", uid))
        conn.commit(); conn.close()
    return uid, {"Authorization": f"Bearer {_jwt_token(uid)}"}


def _buy(client, monkeypatch, headers, product, txn=None, signed=True):
    from app.services import receipt_verification as _rv
    txn = txn or str(2000000000000000 + uuid.uuid4().int % 10**12)
    monkeypatch.setattr(_rv, "verify_signed_transaction", lambda jws, bundle_ids: {
        "bundleId": "com.weirtech.shouldersurf", "originalTransactionId": txn,
        "transactionId": txn, "environment": "Sandbox", "productId": product})
    body = {"product_id": product, "transaction_id": txn}
    if signed:
        body["signed_transaction"] = "j.w.s"
    return client.post("/v1/verify-receipt", json=body, headers=headers), txn


def _features(client, headers):
    r = client.get("/v1/usage/me", headers=headers)
    assert r.status_code == 200, r.text
    f = r.json()["features"]
    return f.get("unlock_apple_cloud"), f.get("unlock_byok")


def _row(db, sql, *args):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


# --- purchase and the gate -----------------------------------------------------

def test_a_pack_grants_its_feature_and_leaves_the_tier_alone(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path)
    assert _features(client, h) == ("disabled", "disabled")
    r, _ = _buy(client, monkeypatch, h, APPLE)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "granted", "unlocks": ["unlock_apple_cloud"]}
    assert _features(client, h) == ("enabled", "disabled")
    assert _row(tmp_db_path, "SELECT tier, original_transaction_id FROM users WHERE id=?", uid) == ("free", None)


def test_the_bundle_grants_both(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    r, _ = _buy(client, monkeypatch, h, BOTH)
    assert r.json()["unlocks"] == ["unlock_apple_cloud", "unlock_byok"]
    assert _features(client, h) == ("enabled", "enabled")


def test_an_unsigned_unlock_is_refused(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    r, _ = _buy(client, monkeypatch, h, APPLE, signed=False)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "signed_transaction_required"
    assert _features(client, h) == ("disabled", "disabled")


def test_reposting_on_restore_is_idempotent(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _, txn = _buy(client, monkeypatch, h, BYOK)
    r, _ = _buy(client, monkeypatch, h, BYOK, txn=txn)
    assert r.json() == {"status": "granted", "unlocks": ["unlock_byok"]}
    assert _row(tmp_db_path, "SELECT COUNT(*) FROM purchases")[0] == 1


def test_restore_on_another_account_moves_the_unlock(client, tmp_db_path, monkeypatch):
    _, h1 = _user(tmp_db_path)
    _, h2 = _user(tmp_db_path)
    _, txn = _buy(client, monkeypatch, h1, APPLE)
    _buy(client, monkeypatch, h2, APPLE, txn=txn)
    assert _features(client, h2) == ("enabled", "disabled")
    assert _features(client, h1) == ("disabled", "disabled")


# --- Scott's "complete the pair" rulings -----------------------------------------

def test_complete_with_one_pack_grants_the_other(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, APPLE)
    r, _ = _buy(client, monkeypatch, h, COMPLETE)
    assert r.json() == {"status": "granted", "unlocks": ["unlock_apple_cloud", "unlock_byok"]}


def test_complete_with_no_pack_waits_then_grants_when_a_pack_arrives(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    r, _ = _buy(client, monkeypatch, h, COMPLETE)
    assert r.json() == {"status": "pending", "unlocks": []}
    _buy(client, monkeypatch, h, BYOK)
    assert _features(client, h) == ("enabled", "enabled")


def test_complete_with_both_grants_nothing_and_is_flagged(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, BOTH)
    r, txn = _buy(client, monkeypatch, h, COMPLETE)
    assert r.json()["status"] == "refund_flagged"
    assert _row(tmp_db_path, "SELECT status, grants FROM purchases WHERE transaction_id=?", txn) == ("refund_flagged", "[]")


# --- the webhook: an unlock refund never touches the subscription ------------------

def _notify(client, monkeypatch, ntype, product, txn, token=None):
    import app.routers.apple_webhooks as W
    n = {"notificationType": ntype, "subtype": "", "data": {
        "bundleId": BUNDLE, "environment": "Sandbox",
        "signedTransactionInfo": {"originalTransactionId": txn, "transactionId": txn,
                                  "productId": product, "appAccountToken": token,
                                  "purchaseDate": 1760000000000}}}
    monkeypatch.setattr(W, "decode_notification", lambda payload, bid: n)
    return client.post("/v1/apple-notifications", json={"signedPayload": "x.y.z"})


def test_refunding_an_unlock_revokes_it_and_keeps_the_subscription(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path, tier="pro")
    _, txn = _buy(client, monkeypatch, h, APPLE)
    r = _notify(client, monkeypatch, "REFUND", APPLE, txn)
    assert r.json()["action"] == "unlock_revoked"
    assert _row(tmp_db_path, "SELECT tier FROM users WHERE id=?", uid) == ("pro",)
    assert _features(client, h) == ("disabled", "disabled")


def test_a_subscription_refund_still_downgrades(client, tmp_db_path, monkeypatch):
    """The unlock branch must not swallow the subscription path."""
    uid, _ = _user(tmp_db_path, tier="pro")
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET original_transaction_id='sub-otid' WHERE id=?", (uid,))
    conn.commit(); conn.close()
    r = _notify(client, monkeypatch, "REFUND", PRO_SUB, "sub-otid")
    assert r.json()["action"] == "refunded"
    assert _row(tmp_db_path, "SELECT tier FROM users WHERE id=?", uid) == ("free",)


def test_one_time_charge_records_by_app_account_token(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path)
    r = _notify(client, monkeypatch, "ONE_TIME_CHARGE", BYOK, "2000000999999999", token=uid.upper())
    assert r.json()["action"] == "unlock_granted"
    assert _features(client, h) == ("disabled", "enabled")


# --- anonymous merge ------------------------------------------------------------

def test_merge_takes_the_union_of_unlocks(client, tmp_db_path, monkeypatch):
    import asyncio
    import aiosqlite
    from app.services.anonymous_merge import merge_into
    anon, ha = _user(tmp_db_path, anonymous=True)
    apple, hp = _user(tmp_db_path)
    _buy(client, monkeypatch, ha, APPLE)
    _buy(client, monkeypatch, hp, BYOK)

    async def go():
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            a = dict(await (await db.execute("SELECT * FROM users WHERE id=?", (anon,))).fetchone())
            p = dict(await (await db.execute("SELECT * FROM users WHERE id=?", (apple,))).fetchone())
            return await merge_into(db, a, p)
    out = asyncio.run(go())
    assert out["moved"]["purchases"] == 1
    assert _features(client, hp) == ("enabled", "enabled")


# --- Project Chat for free, new builds ----------------------------------------------

def _check(client, headers, provider=None, session=None, model="external"):
    body = {"selected_model": model}
    if provider:
        body["selected_provider"] = provider
    if session:
        body["session_id"] = session
    r = client.post("/v1/features/project-chat/check", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_old_builds_keep_todays_verdicts(client, tmp_db_path):
    _, h = _user(tmp_db_path)
    assert _check(client, h)["verdict"] == "send_to_gp"


def test_free_on_apple_cloud_needs_the_pack(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    out = _check(client, h, "apple_cloud")
    assert out["verdict"] == "unlock_required"
    assert out["cta"]["unlock"] == "unlock_apple_cloud" and out["cta"]["switch_to_ssai"] is True
    _buy(client, monkeypatch, h, APPLE)
    assert _check(client, h, "apple_cloud")["verdict"] == "send_to_user_model"
    assert _check(client, h, "byok")["cta"]["unlock"] == "unlock_byok"


def test_free_on_device_is_offered_only_the_switch(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, BOTH)
    out = _check(client, h, "on_device")
    assert out["verdict"] == "unlock_required" and out["cta"]["unlock"] is None


def test_anonymous_free_gets_no_taste(client, tmp_db_path):
    _, h = _user(tmp_db_path, anonymous=True)
    assert _check(client, h, "ssai", model="ssai")["verdict"] == "login_required"


def test_the_free_taste_is_one_conversation_a_month(client, tmp_db_path):
    from app.services.project_chat_policy import record_taste
    import asyncio
    import aiosqlite
    uid, h = _user(tmp_db_path)
    first = _check(client, h, "ssai", "S1", model="ssai")
    assert first["verdict"] == "send_to_gp" and first["quota"]["used"] == 0

    async def count(session):
        async with aiosqlite.connect(tmp_db_path) as db:
            await record_taste(db, uid, session)
    asyncio.run(count("S1"))
    assert _check(client, h, "ssai", "S1", model="ssai")["verdict"] == "send_to_gp"
    second = _check(client, h, "ssai", "S2", model="ssai")
    assert second["verdict"] == "quota_exhausted" and second["cta"]["kind"] == "quota_exhausted"


def test_paid_tiers_are_untouched_by_provider(client, tmp_db_path):
    _, h = _user(tmp_db_path, tier="plus")
    assert _check(client, h, "apple_cloud")["verdict"] == "send_to_user_model"
    assert _check(client, h, "ssai", model="ssai")["verdict"] == "send_to_gp"


def test_tiers_serves_the_pack_copy(client):
    r = client.get("/v1/tiers")
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()["unlocks"]]
    assert ids == ["unlock_apple_cloud", "unlock_byok", "bundle", "complete"]
