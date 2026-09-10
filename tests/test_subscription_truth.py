"""Money is recognised only where Apple reported a non-zero charge (Scott,
2026-09-10). Apple's notification history for Production showed every
transaction was a FREE_TRIAL or an offer code at price 0 while the tab read
$34.97 MRR and "Paid now 3". Two defects behind it: GP recorded price_usd
as LIST price on every event, and GP ignored DID_CHANGE_RENEWAL_STATUS, so a
cancelled trial looked identical to a running one.

These test the truth fields end to end at GP's hop: a decoded Apple payload
in, the stored row and the dashboard's classification out. Apple itself is
faked at `decode_notification`, the single decode point, and at
`get_subscription_state` for the refresh.
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid

import aiosqlite
import pytest

from app.services import subscriptions as subs
from app.services.subscriptions import classify_status, money_fields_from_apple
from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}
PLUS = "com.weirtech.shouldersurf.sub.plus.monthly"
BUNDLE = "com.test.app"


# --- the unit rules -------------------------------------------------------------

def test_apple_price_is_milliunits_and_missing_stays_missing():
    m = money_fields_from_apple(
        {"price": 9990, "currency": "USD", "offerType": 1, "offerDiscountType": "FREE_TRIAL"},
        {"autoRenewStatus": 0})
    assert m == {"price_paid": 9.99, "currency": "USD", "offer_type": 1,
                 "offer_discount_type": "FREE_TRIAL", "auto_renew_status": 0}
    assert money_fields_from_apple({}, {}) == {
        "price_paid": None, "currency": None, "offer_type": None,
        "offer_discount_type": None, "auto_renew_status": None}, \
        "None means Apple did not say; it must never default to free or to paid"


@pytest.mark.parametrize("row,expected", [
    ({"status": 1, "auto_renew_status": 1, "price_paid": 0, "offer_discount_type": "FREE_TRIAL"}, "trialing"),
    ({"status": 1, "auto_renew_status": 0, "price_paid": 0, "offer_discount_type": "FREE_TRIAL"}, "trial_cancelled"),
    ({"status": 1, "auto_renew_status": 1, "price_paid": 9.99, "currency": "USD"}, "paying"),
    ({"status": 1, "auto_renew_status": 0, "price_paid": 9.99}, "paying_cancelled"),
    ({"status": 1, "auto_renew_status": 1, "price_paid": 0, "offer_type": 3}, "on_offer"),
    ({"status": 2, "auto_renew_status": 0, "price_paid": 0, "offer_discount_type": "FREE_TRIAL"}, "trial_lapsed"),
    ({"status": 2, "price_paid": 0, "offer_type": 3}, "offer_lapsed"),
    ({"status": 1, "auto_renew_status": 1, "price_paid": None}, "active_unpriced"),
])
def test_classification_reads_only_what_apple_said(row, expected):
    assert classify_status(row) == expected


def test_a_free_trial_on_a_paid_tier_is_never_paying():
    """The whole point. Tier says plus; Apple says price 0. Not revenue."""
    assert classify_status({"status": 1, "auto_renew_status": 1, "price_paid": 0.0,
                            "offer_discount_type": "FREE_TRIAL", "tier": "plus"}) != "paying"


# --- the notification hop ---------------------------------------------------------

def _seed_user(db_path, otid):
    uid = "truth-" + uuid.uuid4().hex[:8]
    _insert_user(db_path, uid, tier="free")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET original_transaction_id=? WHERE id=?", (otid, uid))
    conn.commit(); conn.close()
    return uid


def _notification(ntype, subtype, otid, *, price=0, currency="USD", offer_type=1,
                  discount="FREE_TRIAL", auto_renew=1, expires_ms=4102444800000):
    return {
        "notificationType": ntype, "subtype": subtype,
        "data": {
            "bundleId": BUNDLE, "environment": "Production",
            "signedTransactionInfo": {
                "originalTransactionId": otid, "transactionId": otid, "productId": PLUS,
                "environment": "Production", "expiresDate": expires_ms,
                "price": price, "currency": currency, "offerType": offer_type,
                "offerDiscountType": discount,
            },
            "signedRenewalInfo": {"autoRenewStatus": auto_renew, "originalTransactionId": otid},
        },
    }


def _post(client, monkeypatch, notification):
    import app.routers.apple_webhooks as W
    monkeypatch.setattr(W, "decode_notification", lambda payload, bid: notification)
    return client.post("/v1/apple-notifications", json={"signedPayload": "x.y.z"})


def _rows(db_path, sql, *args):
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def test_a_free_trial_subscribed_records_price_zero_not_list_price(client, tmp_db_path, monkeypatch):
    otid = "otid-" + uuid.uuid4().hex[:6]
    uid = _seed_user(tmp_db_path, otid)
    r = _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", otid))
    assert r.status_code == 200, r.text
    ev = _rows(tmp_db_path, "SELECT * FROM subscription_events WHERE user_id=?", uid)
    assert len(ev) == 1
    assert ev[0]["price_paid"] == 0.0, "Apple said 0; GP used to write 9.99 here"
    assert ev[0]["price_usd"] == 9.99, "list-price bookkeeping stays what it was"
    assert ev[0]["offer_discount_type"] == "FREE_TRIAL"
    assert ev[0]["auto_renew_status"] == 1
    st = _rows(tmp_db_path, "SELECT * FROM subscription_status WHERE original_transaction_id=?", otid)
    assert len(st) == 1 and st[0]["paid_ever"] == 0 and st[0]["tier"] == "plus"
    assert classify_status(st[0]) == "trialing"


def test_a_renewal_status_change_is_recorded_and_flips_the_status_row(client, tmp_db_path, monkeypatch):
    """Apple sent five of these to Production and GP had recorded none, so a
    cancelled trial was indistinguishable from a running one."""
    otid = "otid-" + uuid.uuid4().hex[:6]
    uid = _seed_user(tmp_db_path, otid)
    _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", otid))
    r = _post(client, monkeypatch, _notification("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", otid, auto_renew=0))
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "renewal_status"
    ev = _rows(tmp_db_path, "SELECT event_type, auto_renew_status, from_tier, to_tier FROM subscription_events WHERE user_id=? ORDER BY effective_at", uid)
    assert ev[-1]["event_type"] == "renewal_status_changed"
    assert ev[-1]["auto_renew_status"] == 0
    assert ev[-1]["from_tier"] == ev[-1]["to_tier"] == "plus", "no tier change on a cancellation"
    st = _rows(tmp_db_path, "SELECT * FROM subscription_status WHERE original_transaction_id=?", otid)[0]
    assert st["auto_renew_status"] == 0
    assert classify_status(st) == "trial_cancelled"
    assert _rows(tmp_db_path, "SELECT tier FROM users WHERE id=?", uid)[0]["tier"] == "plus", \
        "access continues until the period ends"


def test_the_subtype_decides_when_the_renewal_payload_is_thin(client, tmp_db_path, monkeypatch):
    otid = "otid-" + uuid.uuid4().hex[:6]
    _seed_user(tmp_db_path, otid)
    _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", otid))
    n = _notification("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", otid)
    n["data"]["signedRenewalInfo"] = {}
    _post(client, monkeypatch, n)
    st = _rows(tmp_db_path, "SELECT auto_renew_status FROM subscription_status WHERE original_transaction_id=?", otid)[0]
    assert st["auto_renew_status"] == 0


# --- the headline ------------------------------------------------------------------

def _truth(client):
    r = client.get("/webhooks/admin/subscriptions", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["truth"]


def test_trials_and_offers_recognise_no_mrr(client, tmp_db_path, monkeypatch):
    """Prod's exact shape on 2026-09-10: six free trials (one cancelled here),
    an offer code, and a list-price run-rate that used to read as revenue."""
    a, b, c = ("otid-" + uuid.uuid4().hex[:6] for _ in range(3))
    for o in (a, b, c):
        _seed_user(tmp_db_path, o)
    _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", a))
    _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", b))
    _post(client, monkeypatch, _notification("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", b, auto_renew=0))
    _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", c, offer_type=3, discount=None))
    t = _truth(client)
    assert t["paying_now"] == 0
    assert t["mrr_recognized_usd"] == 0.0
    assert t["received_to_date"] == {}
    assert t["trialing_auto_renew_on"] == 1
    assert t["trialing_cancelled"] == 1
    assert t["on_offer"] == 1
    states = {s["original_transaction_id"]: s["state"] for s in t["subscribers"]}
    assert states[a] == "trialing" and states[b] == "trial_cancelled" and states[c] == "on_offer"


def test_a_real_renewal_is_the_first_thing_that_counts(client, tmp_db_path, monkeypatch):
    otid = "otid-" + uuid.uuid4().hex[:6]
    _seed_user(tmp_db_path, otid)
    _post(client, monkeypatch, _notification("SUBSCRIBED", "INITIAL_BUY", otid))
    assert _truth(client)["paying_now"] == 0
    r = _post(client, monkeypatch, _notification("DID_RENEW", None, otid, price=9990, offer_type=None, discount=None))
    assert r.status_code == 200, r.text
    t = _truth(client)
    assert t["paying_now"] == 1
    assert t["mrr_recognized_usd"] == 9.99
    assert t["received_to_date"] == {"USD": 9.99}
    st = [s for s in t["subscribers"] if s["original_transaction_id"] == otid][0]
    assert st["state"] == "paying" and st["paid_ever"] is True and st["price_paid"] == 9.99


def test_sandbox_money_never_reaches_the_headline(client, tmp_db_path, monkeypatch):
    otid = "otid-" + uuid.uuid4().hex[:6]
    _seed_user(tmp_db_path, otid)
    n = _notification("DID_RENEW", None, otid, price=9990, offer_type=None, discount=None)
    n["data"]["environment"] = "Sandbox"; n["data"]["signedTransactionInfo"]["environment"] = "Sandbox"
    _post(client, monkeypatch, n)
    t = _truth(client)
    assert t["paying_now"] == 0 and t["mrr_recognized_usd"] == 0.0 and t["received_to_date"] == {}
    assert any(s["original_transaction_id"] == otid and s["environment"] == "Sandbox" for s in t["subscribers"]), \
        "shown in the table, never in the money"


# --- the refresh from Apple ---------------------------------------------------------

def test_refresh_backfills_events_recorded_before_gp_captured_money(client, tmp_db_path, monkeypatch):
    """Every Production event before 2026-09-10 has price_paid NULL and
    price_usd 9.99. Apple's status call vouches for each transaction id, and
    only NULLs are filled: a value ASSN wrote is never overwritten."""
    from app.services import app_store_server_api as assa
    otid = "otid-" + uuid.uuid4().hex[:6]
    uid = _seed_user(tmp_db_path, otid)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("""INSERT INTO subscription_events (id, user_id, event_type, to_tier, product_id,
                    original_transaction_id, transaction_id, environment, source, price_usd, effective_at, recorded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (uuid.uuid4().hex, uid, "subscribed", "plus", PLUS, otid, otid, "Production", "assn", 9.99,
                  "2026-09-02T19:18:00+00:00", "2026-09-02T19:18:00+00:00"))
    conn.commit(); conn.close()

    async def fake_state(o):
        assert o == otid
        return {"entitled": False, "status": 2, "tier": "plus", "product_id": PLUS,
                "expires_at": "2026-09-09T19:17:59+00:00", "environment": "Production",
                "original_transaction_id": o, "transaction_id": o, "price_paid": 0.0, "currency": "USD",
                "offer_type": 1, "offer_discount_type": "FREE_TRIAL", "auto_renew_status": 0,
                "transactions": [{"transaction_id": o, "price_paid": 0.0, "currency": "USD",
                                  "offer_type": 1, "offer_discount_type": "FREE_TRIAL"}]}
    monkeypatch.setattr(assa, "is_configured", lambda: True)
    monkeypatch.setattr(assa, "get_subscription_state", fake_state)
    r = client.post("/webhooks/admin/subscriptions/refresh-status", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["checked"] == 1 and r.json()["updated"] == 1 and r.json()["backfilled_events"] == 1
    ev = _rows(tmp_db_path, "SELECT price_paid, currency, offer_discount_type, price_usd FROM subscription_events WHERE user_id=?", uid)[0]
    assert ev["price_paid"] == 0.0 and ev["currency"] == "USD" and ev["offer_discount_type"] == "FREE_TRIAL"
    assert ev["price_usd"] == 9.99, "bookkeeping untouched"
    t = _truth(client)
    st = [s for s in t["subscribers"] if s["original_transaction_id"] == otid][0]
    assert st["state"] == "trial_lapsed" and st["source"] == "apple_status"


def test_refresh_reports_a_subscription_apple_does_not_know(client, tmp_db_path, monkeypatch):
    from app.services import app_store_server_api as assa
    otid = "otid-" + uuid.uuid4().hex[:6]
    _seed_user(tmp_db_path, otid)
    async def none_state(o):
        return None
    monkeypatch.setattr(assa, "is_configured", lambda: True)
    monkeypatch.setattr(assa, "get_subscription_state", none_state)
    r = client.post("/webhooks/admin/subscriptions/refresh-status", headers=ADMIN)
    assert r.json()["missing_at_apple"] == [otid]
    assert r.json()["updated"] == 0
