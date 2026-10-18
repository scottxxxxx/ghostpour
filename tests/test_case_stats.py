"""N-400 usage and purchase stats (Scott, 2026-10-18).

Apple's notifications for the N-400 bundle are written to a record-only
ledger and never touch an account; the dashboard reads usage from interview
turns and ties a purchase to a case only when one case was active around it.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import _insert_user

N400 = "com.weirtech.n400helper"
ADMIN = {"X-Admin-Key": "test-admin-key"}
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _notify(client, monkeypatch, ntype, txn, bundle=N400, product="com.weirtech.n400helper.application",
            purchase=T0, price=29990):
    import app.routers.apple_webhooks as W
    n = {"notificationType": ntype, "subtype": "", "data": {
        "bundleId": bundle, "environment": "Production",
        "signedTransactionInfo": {"originalTransactionId": txn, "transactionId": txn,
                                  "productId": product,
                                  "appAccountToken": "8F0E7C5A-0000-8000-8000-000000000001",
                                  "purchaseDate": int(purchase.timestamp() * 1000),
                                  "price": price, "currency": "USD", "storefront": "USA"}}}
    monkeypatch.setattr(W, "decode_notification", lambda payload, bid: n)
    return client.post("/v1/apple-notifications", json={"signedPayload": "x.y.z"})


def _rows(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _turns(db, uid, case_id, times, app="n400"):
    conn = sqlite3.connect(db)
    for t in times:
        conn.execute(
            "INSERT INTO usage_log (id, user_id, provider, model, request_timestamp, app_id, case_id) "
            "VALUES (?, ?, 'anthropic', 'm', ?, ?, ?)",
            (uuid.uuid4().hex, uid, t.isoformat(), app, case_id))
    conn.commit(); conn.close()


# --- the ledger -------------------------------------------------------------

def test_an_n400_purchase_is_recorded_and_touches_no_account(client, tmp_db_path, monkeypatch):
    r = _notify(client, monkeypatch, "ONE_TIME_CHARGE", "t-1")
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "ledger_recorded"
    assert _rows(tmp_db_path, "SELECT app_id, product_id, price_milli, currency, storefront, "
                              "app_account_token, environment FROM app_purchases") == [
        ("n400", "com.weirtech.n400helper.application", 29990, "USD", "USA",
         "8f0e7c5a-0000-8000-8000-000000000001", "Production")]
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM purchases") == [(0,)]
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM subscription_events") == [(0,)]


def test_apple_retrying_a_purchase_writes_one_row(client, tmp_db_path, monkeypatch):
    _notify(client, monkeypatch, "ONE_TIME_CHARGE", "t-1")
    assert _notify(client, monkeypatch, "ONE_TIME_CHARGE", "t-1").json()["action"] == "ledger_duplicate"
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM app_purchases") == [(1,)]


def test_a_refund_marks_the_purchase(client, tmp_db_path, monkeypatch):
    _notify(client, monkeypatch, "ONE_TIME_CHARGE", "t-1")
    assert _notify(client, monkeypatch, "REFUND", "t-1").json()["action"] == "ledger_revoked"
    assert _rows(tmp_db_path, "SELECT revoke_reason FROM app_purchases") == [("refund",)]


def test_a_bundle_outside_both_lists_is_still_refused(client, monkeypatch):
    r = _notify(client, monkeypatch, "ONE_TIME_CHARGE", "t-9", bundle="com.someone.else")
    assert r.status_code == 400


def test_the_n400_bundle_stays_out_of_sign_in_with_apple():
    """The ledger's door is apps.yml, never CZ_APPLE_BUNDLE_ID."""
    from app.config import get_settings
    assert N400 not in (get_settings().apple_bundle_id or "")
    from app.services.app_purchases import ledger_bundles
    assert ledger_bundles() == {N400: "n400"}


# --- the rules ----------------------------------------------------------------

def test_sessions_split_on_a_long_gap_and_idle_time_never_counts():
    from app.services.case_stats import sessions_and_active
    m = lambda x: T0 + timedelta(minutes=x)
    # 0, 2, 4 (4 active min), a 6 minute pause (not active), 10, then 2 days later
    assert sessions_and_active([m(0), m(2), m(4), m(10), m(2 * 1440)]) == (2, 4.0)
    assert sessions_and_active([]) == (0, 0.0)


def test_a_purchase_ties_only_to_a_lone_active_case():
    from app.services.case_stats import match_purchase
    m = lambda x: T0 + timedelta(minutes=x)
    assert match_purchase(m(0), {"a": [m(-30)], "b": [m(-300)]}) == ("matched", "a")
    assert match_purchase(m(0), {"a": [m(-30)], "b": [m(-10)]}) == ("ambiguous", None)
    assert match_purchase(m(0), {"a": [m(-61)]}) == ("unmatched", None)


# --- the endpoint -----------------------------------------------------------------

def test_the_dashboard_reads_usage_and_infers_time_to_purchase(client, tmp_db_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=3)
    m = lambda x: start + timedelta(minutes=x)
    _insert_user(tmp_db_path, user_id="u-a", tier="free", monthly_limit=2.0)
    _insert_user(tmp_db_path, user_id="u-b", tier="free", monthly_limit=2.0)
    _insert_user(tmp_db_path, user_id="u-test", tier="free", monthly_limit=2.0)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET exclude_from_stats = 1 WHERE id = 'u-test'")
    conn.commit(); conn.close()
    # Case A: two sessions a day apart, buys 20 minutes into the second.
    _turns(tmp_db_path, "u-a", "caseA", [m(0), m(2), m(4), m(1440), m(1442), m(1460)])
    # Case B: never buys.
    _turns(tmp_db_path, "u-b", "caseB", [m(600), m(601)])
    # A flagged test account's case is left out entirely.
    _turns(tmp_db_path, "u-test", "caseT", [m(1450)])
    _notify(client, monkeypatch, "ONE_TIME_CHARGE", "t-1", purchase=m(1462))

    r = client.get("/webhooks/admin/telemetry/cases?days=30&app=n400", headers=ADMIN)
    assert r.status_code == 200, r.text
    a = r.json()["apps"][0]
    assert a["app_id"] == "n400"
    assert a["usage"]["users"] == 2 and a["usage"]["cases"] == 2 and a["usage"]["turns"] == 8
    assert a["purchases"]["count"] == 1
    assert a["purchases"]["revenue"] == [{"currency": "USD", "total": 29.99}]
    t = a["to_purchase_inferred"]
    assert t["match_counts"] == {"matched": 1, "ambiguous": 0, "unmatched": 0}
    assert t["median_minutes_first_turn_to_purchase"] == 1462.0
    # 0-2-4 is 4 minutes, 1440-1442 is 2, and the 18 minute pause to 1460 is idle.
    assert t["median_active_minutes_before_purchase"] == 6.0
    assert t["median_sessions_before_purchase"] == 2


def test_the_case_read_never_touches_the_table(client, tmp_db_path):
    from app.services.case_stats import CASE_ROWS_SQL
    conn = sqlite3.connect(tmp_db_path)
    try:
        plan = " | ".join(r[-1] for r in conn.execute(
            "EXPLAIN QUERY PLAN " + CASE_ROWS_SQL, ("n400", "2026-09-01")).fetchall())
    finally:
        conn.close()
    assert "USING COVERING INDEX idx_usage_app_case_ts" in plan, plan
