"""One-time unlock purchases on the dashboard (Scott, 2026-10-22).

The purchases table had every unlock since 10-13 and nothing read it. The
Subscriptions tab now does, through /webhooks/admin/subscriptions/unlocks.
Money comes off Apple's signed transaction only (price in milliunits), from
whichever of the client's verify-receipt or the webhook carries it first.
Revenue counts Production and leaves out Sandbox and refunds, as the
subscription cards do: a TestFlight purchase is not money.
"""
from __future__ import annotations

import sqlite3
import uuid

from tests.conftest import _insert_user, _jwt_token

APPLE = "com.weirtech.shouldersurf.unlock.applecloud"
BYOK = "com.weirtech.shouldersurf.unlock.byok"
ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/subscriptions/unlocks"


def _user(db):
    uid = "u-" + uuid.uuid4().hex[:8]
    _insert_user(db, user_id=uid, tier="free", monthly_limit=2.0)
    return uid, {"Authorization": f"Bearer {_jwt_token(uid)}"}


def _txn():
    return str(2000000000000000 + uuid.uuid4().int % 10**12)


def _buy(client, monkeypatch, headers, product, txn, env="Production", price=None, currency=None):
    from app.services import receipt_verification as _rv
    payload = {"bundleId": "com.weirtech.shouldersurf", "originalTransactionId": txn,
               "transactionId": txn, "environment": env, "productId": product}
    if price is not None:
        payload.update(price=price, currency=currency)
    monkeypatch.setattr(_rv, "verify_signed_transaction", lambda jws, bundle_ids: payload)
    r = client.post("/v1/verify-receipt", headers=headers,
                    json={"product_id": product, "transaction_id": txn, "signed_transaction": "j.w.s"})
    assert r.status_code == 200, r.text


def _notify(client, monkeypatch, ntype, product, txn, token=None, env="Production", price=None, currency=None):
    import app.routers.apple_webhooks as W
    info = {"originalTransactionId": txn, "transactionId": txn, "productId": product,
            "appAccountToken": token, "purchaseDate": 1760000000000, "environment": env}
    if price is not None:
        info.update(price=price, currency=currency)
    n = {"notificationType": ntype, "subtype": "", "data": {
        "bundleId": "com.test.app", "environment": env, "signedTransactionInfo": info}}
    monkeypatch.setattr(W, "decode_notification", lambda payload, bid: n)
    return client.post("/v1/apple-notifications", json={"signedPayload": "x.y.z"})


def _money(db, txn):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT price_paid, currency FROM purchases WHERE transaction_id=?", (txn,)).fetchone()
    finally:
        conn.close()


def test_the_signed_purchase_records_what_apple_charged(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    txn = _txn()
    _buy(client, monkeypatch, h, APPLE, txn, price=9990, currency="USD")
    assert _money(tmp_db_path, txn) == (9.99, "USD")


def test_the_webhook_fills_the_price_the_client_post_lacked(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path)
    txn = _txn()
    _buy(client, monkeypatch, h, APPLE, txn)
    assert _money(tmp_db_path, txn) == (None, None)
    r = _notify(client, monkeypatch, "ONE_TIME_CHARGE", APPLE, txn, token=uid, price=9990, currency="USD")
    assert r.json()["action"] == "unlock_granted"
    assert _money(tmp_db_path, txn) == (9.99, "USD")


def test_a_later_post_never_overwrites_a_recorded_price(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path)
    txn = _txn()
    _buy(client, monkeypatch, h, APPLE, txn, price=9990, currency="USD")
    _notify(client, monkeypatch, "ONE_TIME_CHARGE", APPLE, txn, token=uid, price=1, currency="EUR")
    assert _money(tmp_db_path, txn) == (9.99, "USD")


def test_the_report_counts_production_money_only(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    paid, sandbox, refunded = _txn(), _txn(), _txn()
    _buy(client, monkeypatch, h, APPLE, paid, price=9990, currency="USD")
    _buy(client, monkeypatch, h, BYOK, sandbox, env="Sandbox", price=9990, currency="USD")
    _, h2 = _user(tmp_db_path)
    _buy(client, monkeypatch, h2, BYOK, refunded, price=9990, currency="USD")
    _notify(client, monkeypatch, "REFUND", BYOK, refunded)

    d = client.get(URL, headers=ADMIN).json()
    assert d["revenue_production"] == {"USD": 9.99}
    assert d["totals"]["production"] == 2 and d["totals"]["sandbox"] == 1 and d["totals"]["refunded"] == 1
    by = {b["product_id"]: b for b in d["by_product"]}
    assert (by[APPLE]["production"], by[APPLE]["sandbox"], by[APPLE]["refunded"]) == (1, 0, 0)
    assert (by[BYOK]["production"], by[BYOK]["sandbox"], by[BYOK]["refunded"]) == (1, 1, 1)
    assert {r["transaction_id"] for r in d["recent"]} == {paid, sandbox, refunded}


def test_the_report_needs_the_admin_key(client):
    assert client.get(URL, headers={"X-Admin-Key": "nope"}).status_code == 403
