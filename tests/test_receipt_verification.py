"""A purchase is real when Apple says so, not when the client does.

Every test here is anchored to something that actually happened on
2026-08-28: an Xcode run against the local StoreKit configuration file
granted Pro, booked $14.99 of MRR against a purchase Apple has no record
of, and wrote `original_transaction_id = '0'` over a good id.

The assertions are deliberately about STORED STATE and not about a 200,
because a 200 was exactly what the broken path returned. Check the echo,
not the status.
"""

import sqlite3

import pytest

from app.services import receipt_verification as rv

REAL_OTID = "2000001211148772"      # a real id off a real row
REAL_TXN = "2000001216894003"       # a later renewal of the same subscription


def _otid_of(db_path, user_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT original_transaction_id FROM users WHERE id = ?",
            (user_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_otid(db_path, user_id, otid):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET original_transaction_id = ? WHERE id = ?",
            (otid, user_id))
        conn.commit()
    finally:
        conn.close()


def _events(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT event_type, transaction_id, original_transaction_id, environment "
            "FROM subscription_events WHERE user_id = ? ORDER BY recorded_at",
            (user_id,))]
    finally:
        conn.close()


# --- the id predicate ------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "0",            # what Xcode's local StoreKit configuration reports
    "00",           # still zero, and int() would agree
    "",
    "   ",
    None,
    12345,          # an int is not what the wire carries
    "txn-offer-1",  # our own old test fixtures, which no Apple id resembles
    "2000001211148772x",
    "١٢٣",          # str.isdigit() says True; no Apple id is Arabic-Indic
])
def test_implausible_ids_are_refused(bad):
    assert rv.is_plausible_transaction_id(bad) is False


@pytest.mark.parametrize("good", [REAL_OTID, REAL_TXN, "120003884095739", "1"])
def test_real_ids_are_accepted(good):
    assert rv.is_plausible_transaction_id(good) is True


# --- the bundle id check ---------------------------------------------------

def test_signed_transaction_for_another_app_is_refused(monkeypatch):
    """The check that did not exist. `_verify_x5c_chain` takes a bundle_id
    it never reads, so without this a JWS Apple signed for ANY app would
    have verified and bought its holder a subscription."""
    from app.services import apple_notifications

    monkeypatch.setattr(
        apple_notifications, "decode_and_verify_jws",
        lambda jws, bundle_id: {"bundleId": "com.someoneelse.app",
                                "originalTransactionId": REAL_OTID})

    with pytest.raises(apple_notifications.AppleJWSError) as e:
        rv.verify_signed_transaction("j.w.s", {"com.weirtech.shouldersurf"})
    assert "com.someoneelse.app" in str(e.value)


def test_signed_transaction_for_our_app_is_accepted(monkeypatch):
    from app.services import apple_notifications

    monkeypatch.setattr(
        apple_notifications, "decode_and_verify_jws",
        lambda jws, bundle_id: {"bundleId": "com.weirtech.shouldersurf",
                                "originalTransactionId": REAL_OTID})

    payload = rv.verify_signed_transaction("j.w.s", {"com.weirtech.shouldersurf"})
    assert payload["originalTransactionId"] == REAL_OTID


def test_a_gateway_serving_several_apps_accepts_each_of_them(monkeypatch):
    """apple_bundle_id is a comma-joined list. A single-value comparison
    here would refuse the other apps' genuine purchases."""
    from app.services import apple_notifications

    class S:
        apple_bundle_id = "com.weirtech.shouldersurf, com.weirtech.techrehearsal"

    assert rv.allowed_bundle_ids(S) == {
        "com.weirtech.shouldersurf", "com.weirtech.techrehearsal"}

    monkeypatch.setattr(
        apple_notifications, "decode_and_verify_jws",
        lambda jws, bundle_id: {"bundleId": "com.weirtech.techrehearsal",
                                "originalTransactionId": REAL_OTID})
    assert rv.verify_signed_transaction("j.w.s", rv.allowed_bundle_ids(S))


# --- the dial --------------------------------------------------------------

def test_enforcement_is_off_by_default():
    """Live subscribers are on builds that have never sent a JWS. A
    default of True would strip Pro from people who actually paid."""
    assert rv.require_signed_transaction({}, None) is False
    assert rv.require_signed_transaction({"verify-receipt": {}}, None) is False


def test_a_malformed_dial_does_not_start_refusing_purchases():
    assert rv.require_signed_transaction(
        {"verify-receipt": {"require_signed_transaction": "true"}}, None) is False
    assert rv.require_signed_transaction(
        {"verify-receipt": {"require_signed_transaction": 1}}, None) is False


def test_the_dial_turns_on():
    assert rv.require_signed_transaction(
        {"verify-receipt": {"require_signed_transaction": True}}, None) is True


# --- the endpoint, against the real bug ------------------------------------

def test_a_zero_id_does_not_overwrite_a_good_one(client, free_user, tmp_db_path):
    """Scott's row held 120003884095739 and the 08-28 purchase replaced it
    with '0', which is the field Apple's server notifications match on."""
    _set_otid(tmp_db_path, free_user["user_id"], REAL_OTID)

    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": "0",
    }, headers=free_user["headers"])

    assert r.status_code == 200, r.text
    assert _otid_of(tmp_db_path, free_user["user_id"]) == REAL_OTID


def test_a_zero_id_does_not_clear_another_users_id(client, free_user, pro_user,
                                                   tmp_db_path):
    """The cross-account dedup nulls the id off every OTHER row holding it.
    Handed '0' twice it widens: ddc3df33 was left NULL in prod this way."""
    _set_otid(tmp_db_path, pro_user["user_id"], "0")

    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": "0",
    }, headers=free_user["headers"])

    assert r.status_code == 200, r.text
    assert _otid_of(tmp_db_path, pro_user["user_id"]) == "0"


def test_an_unverifiable_receipt_raises_an_incident(client, free_user, tmp_db_path):
    """A machine decision nobody sees is unexamined. This field went bad
    on 08-22 and nothing said a word until 08-28."""
    client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": "0",
    }, headers=free_user["headers"])

    conn = sqlite3.connect(tmp_db_path)
    try:
        rows = conn.execute(
            "SELECT subject, details_json FROM alert_incidents "
            "WHERE category = 'receipt_unverified'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, rows
    assert free_user["user_id"] in rows[0][0]
    assert "'0'" in rows[0][1]


def test_a_good_id_is_still_stored(client, free_user, tmp_db_path):
    """The guard must not become a wall: a real purchase still binds."""
    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": REAL_OTID,
    }, headers=free_user["headers"])

    assert r.status_code == 200, r.text
    assert _otid_of(tmp_db_path, free_user["user_id"]) == REAL_OTID


def test_the_renewal_id_and_the_original_id_are_recorded_separately(
        client, free_user, tmp_db_path):
    """current_transaction_id was sent by SS for months and modelled
    nowhere, so both columns held a second copy of the original."""
    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": REAL_OTID,
        "current_transaction_id": REAL_TXN,
    }, headers=free_user["headers"])
    assert r.status_code == 200, r.text

    events = _events(tmp_db_path, free_user["user_id"])
    assert len(events) == 1, events
    assert events[0]["original_transaction_id"] == REAL_OTID
    assert events[0]["transaction_id"] == REAL_TXN


def test_enforcement_refuses_an_unsigned_receipt(client, free_user, tmp_db_path):
    """With the dial on, the Xcode purchase gets nothing."""
    client.app.state.remote_configs["verify-receipt"] = {
        "require_signed_transaction": True}
    try:
        r = client.post("/v1/verify-receipt", json={
            "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
            "transaction_id": "0",
        }, headers=free_user["headers"])
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "receipt_unverified"

        conn = sqlite3.connect(tmp_db_path)
        try:
            tier = conn.execute(
                "SELECT tier FROM users WHERE id = ?",
                (free_user["user_id"],)).fetchone()[0]
        finally:
            conn.close()
        assert tier == "free"
    finally:
        client.app.state.remote_configs.pop("verify-receipt", None)


def test_apples_product_wins_over_the_clients_claim(client, free_user, tmp_db_path,
                                                    monkeypatch):
    """The hole the signature would otherwise leave open: proving a real
    purchase happened says nothing about WHICH product it was for. A
    genuine Plus receipt presented alongside product_id=pro must buy
    Plus, not Pro."""
    from app.services import receipt_verification as _rv

    monkeypatch.setattr(
        _rv, "verify_signed_transaction",
        lambda jws, bundle_ids: {
            "bundleId": "com.weirtech.shouldersurf",
            "originalTransactionId": REAL_OTID,
            "transactionId": REAL_TXN,
            "environment": "Production",
            "productId": "com.weirtech.shouldersurf.sub.plus.monthly",
        })

    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": REAL_OTID,
        "signed_transaction": "j.w.s",
    }, headers=free_user["headers"])

    assert r.status_code == 200, r.text
    assert r.json()["new_tier"] == "plus"

    conn = sqlite3.connect(tmp_db_path)
    try:
        tier = conn.execute(
            "SELECT tier FROM users WHERE id = ?",
            (free_user["user_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert tier == "plus"


def test_a_verified_receipt_records_apples_environment_not_an_inference(
        client, free_user, tmp_db_path, monkeypatch):
    """Every environment we have ever stored on a verify_receipt row was
    inferred from an EARLIER event, because the signed_transaction branch
    had never once executed. This is that branch running."""
    from app.services import receipt_verification as _rv

    monkeypatch.setattr(
        _rv, "verify_signed_transaction",
        lambda jws, bundle_ids: {
            "bundleId": "com.weirtech.shouldersurf",
            "originalTransactionId": REAL_OTID,
            "transactionId": REAL_TXN,
            "environment": "Sandbox",
            "productId": "com.weirtech.shouldersurf.sub.pro.monthly",
        })

    r = client.post("/v1/verify-receipt", json={
        "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
        "transaction_id": REAL_OTID,
        "signed_transaction": "j.w.s",
    }, headers=free_user["headers"])
    assert r.status_code == 200, r.text

    events = _events(tmp_db_path, free_user["user_id"])
    assert len(events) == 1, events
    assert events[0]["environment"] == "Sandbox"
    assert events[0]["original_transaction_id"] == REAL_OTID
    assert events[0]["transaction_id"] == REAL_TXN


def test_enforcement_accepts_a_verified_receipt(client, free_user, monkeypatch):
    """The dial must refuse the unverifiable without refusing everything."""
    from app.services import receipt_verification as _rv

    monkeypatch.setattr(
        _rv, "verify_signed_transaction",
        lambda jws, bundle_ids: {
            "bundleId": "com.weirtech.shouldersurf",
            "originalTransactionId": REAL_OTID,
            "transactionId": REAL_TXN,
            "environment": "Production",
            "productId": "com.weirtech.shouldersurf.sub.pro.monthly",
        })
    client.app.state.remote_configs["verify-receipt"] = {
        "require_signed_transaction": True}
    try:
        r = client.post("/v1/verify-receipt", json={
            "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
            "transaction_id": REAL_OTID,
            "signed_transaction": "j.w.s",
        }, headers=free_user["headers"])
        assert r.status_code == 200, r.text
        assert r.json()["new_tier"] == "pro"
    finally:
        client.app.state.remote_configs.pop("verify-receipt", None)


# --- the same hole on the push side ----------------------------------------

def _post_notification(client, monkeypatch, bundle_id):
    """Drive /v1/apple-notifications with a decoded payload we control.

    The JWS crypto is not what is under test here; whether we check WHOSE
    app the notification is about is.
    """
    from app.routers import apple_webhooks

    monkeypatch.setattr(
        apple_webhooks, "decode_notification",
        lambda payload, bid: {
            "notificationType": "EXPIRED",
            "subtype": "VOLUNTARY",
            "data": {
                "bundleId": bundle_id,
                "environment": "Production",
                "signedTransactionInfo": {
                    "originalTransactionId": REAL_OTID,
                    "transactionId": REAL_OTID,
                    "productId": "com.weirtech.shouldersurf.sub.pro.monthly",
                },
            },
        })
    return client.post("/v1/apple-notifications",
                       json={"signedPayload": "j.w.s"})


def test_a_notification_for_another_app_is_refused(client, monkeypatch):
    """/v1/apple-notifications is unauthenticated by design, and the JWS
    check only establishes that APPLE signed it, never that Apple signed
    it FOR US. Without this a third-party developer could put one of our
    user ids in their own app's appAccountToken and relay an EXPIRED to
    strip that user's entitlement."""
    r = _post_notification(client, monkeypatch, "com.someoneelse.app")
    assert r.status_code == 400, r.text
    assert "com.someoneelse.app" in r.json()["error"]


def test_a_notification_for_our_app_is_processed(client, monkeypatch):
    """The guard must not become a wall. `com.test.app` is what conftest
    sets CZ_APPLE_BUNDLE_ID to; in prod the same comparison is against
    com.shouldersurf.ShoulderSurf, which real notifications do carry in
    data.bundleId (checked against Apple's Production notification
    history for SUBSCRIBED, DID_CHANGE_RENEWAL_STATUS and EXPIRED before
    this guard was written)."""
    r = _post_notification(client, monkeypatch, "com.test.app")
    assert r.status_code == 200, r.text
