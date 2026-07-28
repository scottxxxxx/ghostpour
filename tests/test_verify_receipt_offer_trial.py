"""Offer-code redemptions are not trials (2026-07-28): StoreKit reports a
free offer code's payment mode as freeTrial, so the client sends
is_trial=true, and gifted Pro accounts rendered 'Trial Usage' with the
$5.10 trial cap. The offer reference name distinguishes: offer_id present
without an introductory offer_type unmarks the trial. A genuine intro
trial (no offer_id) keeps it.
"""

import sqlite3


def _user_row(db_path, user_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT tier, is_trial FROM users WHERE id = ?",
            (user_id,)).fetchone()
    finally:
        conn.close()


def test_offer_code_redemption_is_not_a_trial(client, free_user, tmp_db_path):
    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": "txn-offer-1",
        "is_trial": True,
        "offer_id": "friend-test-offer",
    }, headers=free_user["headers"])
    assert r.status_code == 200, r.text
    tier, is_trial = _user_row(tmp_db_path, free_user["user_id"])
    assert tier == "pro"
    assert is_trial == 0


def test_genuine_intro_trial_still_marks_trial(client, free_user, tmp_db_path):
    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.plus.monthly",
        "transaction_id": "txn-intro-1",
        "is_trial": True,
        "offer_type": "introductory",
        "offer_price": 0,
    }, headers=free_user["headers"])
    assert r.status_code == 200, r.text
    tier, is_trial = _user_row(tmp_db_path, free_user["user_id"])
    assert tier == "plus"
    assert is_trial == 1
