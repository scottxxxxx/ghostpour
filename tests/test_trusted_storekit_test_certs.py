"""Closing the verify-receipt hole without locking the owner out of Xcode.

With `require_signed_transaction` off, anyone can mint an anonymous
account and name a product. Turning it on refuses Scott's own Xcode runs,
because a local StoreKit configuration signs with Xcode's "StoreKit Test"
certificate: one self-signed cert, no chain to Apple. So the served doc
can list the fingerprints of test certificates the team owns, and a
transaction signed by one of them is verified with environment "Xcode".

The certificate here is a throwaway P-256 key and a self-signed cert made
per test run. Nothing below stubs the verifier: every JWS is really
signed and really checked, because the property under test IS the
signature check. A listed fingerprint alone must buy nothing.

The second half covers the Apple lookup for builds before 1.17, which
send no JWS at all. Apple is never called from here.
"""

import base64
import datetime
import hashlib
import json
import logging
import sqlite3
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID

from app.services import receipt_verification as rv

BUNDLE = "com.test.app"          # conftest's CZ_APPLE_BUNDLE_ID
PRO = "com.weirtech.shouldersurf.sub.pro.monthly"
PLUS = "com.weirtech.shouldersurf.sub.plus.monthly"
REAL_OTID = "2000001211148772"
SETTINGS = SimpleNamespace(apple_bundle_id=BUNDLE)


# --- a throwaway "StoreKit Test" certificate --------------------------------

def _make_cert():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "StoreKit Test Certificate")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    return key, der, hashlib.sha256(der).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jws(signing_key, cert_ders, payload):
    """A compact ES256 JWS with `cert_ders` as its x5c, signed by `signing_key`."""
    header = {"alg": "ES256", "x5c": [base64.b64encode(d).decode("ascii") for d in cert_ders]}
    h = _b64url(json.dumps(header).encode())
    p = _b64url(json.dumps(payload).encode())
    der_sig = signing_key.sign(f"{h}.{p}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_sig)
    return f"{h}.{p}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _payload(**over):
    base = {
        "bundleId": BUNDLE,
        "originalTransactionId": "2",
        "transactionId": "5",
        "environment": "Xcode",
        "productId": PRO,
    }
    base.update(over)
    return base


def _body(jws, product_id=PRO, transaction_id="2"):
    return SimpleNamespace(signed_transaction=jws, product_id=product_id,
                           transaction_id=transaction_id, current_transaction_id=None)


def _cfg(*fingerprints, dial=None):
    doc = {"trusted_storekit_test_certs": list(fingerprints)}
    if dial is not None:
        doc["require_signed_transaction"] = dial
    return {"verify-receipt": doc}


@pytest.fixture
def xcode():
    key, der, fingerprint = _make_cert()
    return SimpleNamespace(key=key, der=der, fingerprint=fingerprint)


# --- the verifier -----------------------------------------------------------

def test_a_listed_cert_and_a_good_signature_is_verified_as_xcode(xcode):
    jws = _jws(xcode.key, [xcode.der], _payload())
    identity = rv.resolve_identity(_body(jws), SETTINGS, _cfg(xcode.fingerprint))

    assert identity.verified is True
    assert identity.environment == "Xcode"
    assert identity.is_xcode is True
    # Identity comes off the signed payload, as it does for Apple's.
    assert identity.product_id == PRO
    assert identity.original_transaction_id == "2"
    assert identity.transaction_id == "5"
    assert identity.reject_reason is None


def test_an_unlisted_cert_is_rejected_exactly_as_before(xcode):
    _, _, someone_elses = _make_cert()
    jws = _jws(xcode.key, [xcode.der], _payload())
    identity = rv.resolve_identity(_body(jws), SETTINGS, _cfg(someone_elses))

    assert identity.verified is False
    assert identity.environment == "Unverified"
    assert identity.is_xcode is False
    assert identity.product_id is None
    assert identity.reject_reason == (
        "signature_rejected: x5c chain too short: 1 certs (need >= 3)")


def test_a_listed_cert_with_a_signature_from_another_key_is_rejected(xcode):
    """THE one that matters. A certificate is public: anyone who has seen
    a trusted one can paste it into a header. Only the signature proves
    the sender holds its private key."""
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    forged = _jws(attacker_key, [xcode.der], _payload())
    identity = rv.resolve_identity(_body(forged), SETTINGS, _cfg(xcode.fingerprint))

    assert identity.verified is False
    assert identity.environment == "Unverified"
    assert identity.product_id is None
    assert identity.reject_reason == (
        "signature_rejected: x5c chain too short: 1 certs (need >= 3)")


def test_a_listed_fingerprint_in_a_two_cert_chain_is_not_xcode(xcode):
    """Exactly one certificate, or it is not a local test transaction."""
    jws = _jws(xcode.key, [xcode.der, xcode.der], _payload())
    identity = rv.resolve_identity(_body(jws), SETTINGS, _cfg(xcode.fingerprint))

    assert identity.verified is False
    assert identity.environment == "Unverified"
    assert identity.reject_reason == (
        "signature_rejected: x5c chain too short: 2 certs (need >= 3)")
    assert identity.cert_sha256 is None


def test_the_bundle_id_is_still_checked_for_a_trusted_cert(xcode):
    jws = _jws(xcode.key, [xcode.der], _payload(bundleId="com.someone.else"))
    identity = rv.resolve_identity(_body(jws), SETTINGS, _cfg(xcode.fingerprint))

    assert identity.verified is False
    assert identity.environment == "Unverified"


@pytest.mark.parametrize("missing", [None, {}, {"verify-receipt": {}},
                                     {"verify-receipt": {"trusted_storekit_test_certs": []}}])
def test_a_missing_or_empty_list_trusts_nothing(xcode, missing):
    jws = _jws(xcode.key, [xcode.der], _payload())
    identity = rv.resolve_identity(_body(jws), SETTINGS, missing)

    assert identity.verified is False
    assert identity.environment == "Unverified"


# --- fingerprints -----------------------------------------------------------

def test_fingerprints_compare_without_case_colons_or_spaces(xcode):
    fp = xcode.fingerprint
    pairs = [fp[i:i + 2] for i in range(0, 64, 2)]
    openssl_style = ":".join(pairs).upper()          # openssl x509 -fingerprint
    keychain_style = " ".join(pairs).upper()         # Keychain Access
    padded = f"  {fp.upper()}\n"

    for written in (openssl_style, keychain_style, padded, fp):
        assert rv.normalize_fingerprint(written) == fp
        jws = _jws(xcode.key, [xcode.der], _payload())
        identity = rv.resolve_identity(_body(jws), SETTINGS, _cfg(written))
        assert identity.is_xcode is True, written


@pytest.mark.parametrize("bad", [
    "", "abc", "a" * 63, "a" * 65, "g" * 64, "a" * 62 + "-a", None, 12345,
    ["a" * 64], {"sha256": "a" * 64},
])
def test_an_entry_that_is_not_64_hex_characters_is_refused(bad):
    assert rv.normalize_fingerprint(bad) is None


def test_a_malformed_entry_is_skipped_with_a_warning_and_the_rest_still_count(xcode, caplog):
    cfg = {"verify-receipt": {"trusted_storekit_test_certs": [
        "not a fingerprint", 42, None, "a" * 63, xcode.fingerprint]}}
    with caplog.at_level(logging.WARNING, logger="ghostpour.receipt_verification"):
        trusted = rv.trusted_storekit_test_certs(cfg)

    assert trusted == frozenset({xcode.fingerprint})
    ignored = [r for r in caplog.records
               if "trusted_storekit_test_certs_entry_ignored" in r.getMessage()]
    assert [r.args[0] for r in ignored] == [0, 1, 2, 3]
    # The bad value itself is not echoed into the log.
    assert "not a fingerprint" not in caplog.text


@pytest.mark.parametrize("not_a_list", ["FINGERPRINT", {"a": 1}, 7, True])
def test_a_malformed_list_is_ignored_and_does_not_crash(xcode, not_a_list, caplog):
    """A string is the dangerous shape: iterating it would yield single
    characters, and `in` on it would be a substring test."""
    value = xcode.fingerprint if not_a_list == "FINGERPRINT" else not_a_list
    cfg = {"verify-receipt": {"trusted_storekit_test_certs": value}}
    with caplog.at_level(logging.WARNING, logger="ghostpour.receipt_verification"):
        assert rv.trusted_storekit_test_certs(cfg) == frozenset()
        jws = _jws(xcode.key, [xcode.der], _payload())
        identity = rv.resolve_identity(_body(jws), SETTINGS, cfg)

    assert "trusted_storekit_test_certs_ignored reason=not_a_list" in caplog.text
    assert identity.verified is False
    assert identity.environment == "Unverified"


# --- discoverability --------------------------------------------------------

def test_the_fingerprint_of_a_refused_single_cert_is_logged_and_the_jws_is_not(xcode, caplog):
    jws = _jws(xcode.key, [xcode.der], _payload())
    with caplog.at_level(logging.INFO, logger="ghostpour.receipt_verification"):
        identity = rv.resolve_identity(_body(jws), SETTINGS, {})

    assert identity.cert_sha256 == xcode.fingerprint
    info = [r for r in caplog.records if r.levelno == logging.INFO
            and xcode.fingerprint in r.getMessage()]
    assert len(info) == 1, caplog.text
    header_b64, payload_b64, signature_b64 = jws.split(".")
    for part in (jws, header_b64, payload_b64, signature_b64):
        assert part not in caplog.text


# --- the endpoint -----------------------------------------------------------

def _row(db_path, sql, args=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _tier(db_path, user_id):
    return _row(db_path, "SELECT tier FROM users WHERE id = ?", (user_id,))[0]["tier"]


def _unverified_alerts(db_path):
    return _row(db_path, "SELECT subject, details_json FROM alert_incidents "
                         "WHERE category = 'receipt_unverified'")


@pytest.fixture
def verify_cfg(client):
    """Set the served verify-receipt doc for one test, then put it back."""
    configs = client.app.state.remote_configs
    before = configs.get("verify-receipt")

    def _set(doc):
        configs["verify-receipt"] = doc
    yield _set
    if before is None:
        configs.pop("verify-receipt", None)
    else:
        configs["verify-receipt"] = before


@pytest.fixture
def tier_changes(monkeypatch):
    from app.services import context_quilt as cq
    seen = []

    async def _capture(**kw):
        seen.append(kw)
    monkeypatch.setattr(cq, "notify_tier_change", _capture)
    return seen


def _post(client, user, jws=None, product_id=PRO, transaction_id="2"):
    body = {"product_id": product_id, "transaction_id": transaction_id}
    if jws is not None:
        body["signed_transaction"] = jws
    return client.post("/v1/verify-receipt", json=body, headers=user["headers"])


@pytest.mark.anyio
async def test_an_xcode_purchase_grants_the_tier_and_is_not_revenue(
        client, free_user, tmp_db_path, xcode, verify_cfg, tier_changes):
    import aiosqlite
    from app.services import subscriptions as subs
    verify_cfg({"trusted_storekit_test_certs": [xcode.fingerprint]})

    r = _post(client, free_user, _jws(xcode.key, [xcode.der], _payload()))

    assert r.status_code == 200, r.text
    assert r.json()["new_tier"] == "pro"
    assert _tier(tmp_db_path, free_user["user_id"]) == "pro"

    events = _row(tmp_db_path,
                  "SELECT event_type, environment, transaction_id, original_transaction_id "
                  "FROM subscription_events WHERE user_id = ?", (free_user["user_id"],))
    assert events == [{"event_type": "subscribed", "environment": "Xcode",
                       "transaction_id": "5", "original_transaction_id": "2"}]
    # Verified, so nothing to alert on.
    assert _unverified_alerts(tmp_db_path) == []
    # The purchase alert is told which environment, so it can stay silent.
    assert tier_changes and tier_changes[0]["environment"] == "Xcode", tier_changes

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        summary = await subs.summary(db)
        trend = await subs.mrr_trend(db)
        months = await subs.monthly_aggregates(db)
    assert summary["paid_by_env"] == {"production": 0, "sandbox": 0, "xcode": 1, "unknown": 0}
    assert summary["paid_now"] == 0
    assert summary["current_mrr_gross_usd"] == 0
    assert summary["sandbox_mrr_gross_usd"] == 0
    assert all(d["mrr_gross_usd"] == 0 and d["active_paid"] == 0 for d in trend)
    assert months == []


@pytest.mark.anyio
async def test_an_xcode_purchase_sends_no_purchase_email_or_push(client, app_env):
    """The operator push rides the same incident, so no incident is both."""
    from app.services.subscription_alerts import notify_purchase
    from tests.conftest import _insert_user
    db_path = app_env["CZ_DATABASE_URL"].split("///")[-1]
    _insert_user(db_path, "xcode-buyer")

    await notify_purchase("xcode-buyer", "free", "pro", "upgrade", environment="Xcode")

    assert _row(db_path, "SELECT 1 FROM alert_incidents "
                         "WHERE category = 'subscription_purchase'") == []


def test_an_xcode_id_is_never_bound_to_an_account_or_taken_from_another(
        client, free_user, pro_user, tmp_db_path, xcode, verify_cfg, tier_changes):
    """A local StoreKit configuration numbers transactions per device, so
    two test devices both holding "2" are not one purchase. Verified or
    not, it must not strip the other account."""
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET original_transaction_id = '2' WHERE id = ?",
                 (pro_user["user_id"],))
    conn.commit()
    conn.close()
    verify_cfg({"trusted_storekit_test_certs": [xcode.fingerprint]})

    r = _post(client, free_user, _jws(xcode.key, [xcode.der], _payload()))

    assert r.status_code == 200, r.text
    assert r.json()["moved_from_other_account"] is False
    users = {u["id"]: u for u in _row(
        tmp_db_path, "SELECT id, tier, original_transaction_id FROM users")}
    assert users[pro_user["user_id"]]["tier"] == "pro"
    assert users[pro_user["user_id"]]["original_transaction_id"] == "2"
    assert users[free_user["user_id"]]["tier"] == "pro"
    assert users[free_user["user_id"]]["original_transaction_id"] is None


def test_the_fingerprint_appears_in_the_alert_details(
        client, free_user, tmp_db_path, xcode, tier_changes):
    r = _post(client, free_user, _jws(xcode.key, [xcode.der], _payload()))

    assert r.status_code == 200, r.text      # dial off: today's behaviour
    alerts = _unverified_alerts(tmp_db_path)
    assert len(alerts) == 1, alerts
    details = json.loads(alerts[0]["details_json"])
    assert details["cert_sha256"] == xcode.fingerprint
    assert details["reason"] == (
        "signature_rejected: x5c chain too short: 1 certs (need >= 3)")
    events = _row(tmp_db_path, "SELECT environment FROM subscription_events "
                               "WHERE user_id = ?", (free_user["user_id"],))
    assert events == [{"environment": "Unverified"}]


def test_dial_on_and_an_untrusted_cert_is_a_400(
        client, free_user, tmp_db_path, xcode, verify_cfg, tier_changes):
    _, _, someone_elses = _make_cert()
    verify_cfg({"require_signed_transaction": True,
                "trusted_storekit_test_certs": [someone_elses]})

    r = _post(client, free_user, _jws(xcode.key, [xcode.der], _payload()))

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "receipt_unverified"
    assert _tier(tmp_db_path, free_user["user_id"]) == "free"
    assert tier_changes == []


def test_dial_on_and_a_forged_signature_is_a_400(
        client, free_user, tmp_db_path, xcode, verify_cfg, tier_changes):
    verify_cfg({"require_signed_transaction": True,
                "trusted_storekit_test_certs": [xcode.fingerprint]})
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    r = _post(client, free_user, _jws(attacker_key, [xcode.der], _payload()))

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "receipt_unverified"
    assert _tier(tmp_db_path, free_user["user_id"]) == "free"


@pytest.mark.parametrize("otid", ["2", "0"])
def test_dial_on_and_a_trusted_cert_is_granted(
        client, free_user, tmp_db_path, xcode, verify_cfg, tier_changes, otid):
    """"0" is the first id a fresh local StoreKit configuration hands out.
    It is not an Apple id, and it must not turn the owner's first Xcode
    purchase into a 400 or an alert."""
    verify_cfg({"require_signed_transaction": True,
                "trusted_storekit_test_certs": [xcode.fingerprint]})

    r = _post(client, free_user,
              _jws(xcode.key, [xcode.der],
                   _payload(originalTransactionId=otid, transactionId=otid)),
              transaction_id=otid)

    assert r.status_code == 200, r.text
    assert r.json()["new_tier"] == "pro"
    assert _tier(tmp_db_path, free_user["user_id"]) == "pro"
    assert _unverified_alerts(tmp_db_path) == []
    events = _row(tmp_db_path, "SELECT environment FROM subscription_events "
                               "WHERE user_id = ?", (free_user["user_id"],))
    assert events == [{"environment": "Xcode"}]


def test_the_signed_product_wins_for_xcode_too(
        client, free_user, tmp_db_path, xcode, verify_cfg, tier_changes):
    verify_cfg({"trusted_storekit_test_certs": [xcode.fingerprint]})

    r = _post(client, free_user,
              _jws(xcode.key, [xcode.der], _payload(productId=PLUS)), product_id=PRO)

    assert r.status_code == 200, r.text
    assert _tier(tmp_db_path, free_user["user_id"]) == "plus"


# --- the Apple lookup for builds that send no JWS ---------------------------

def _apple_says(monkeypatch, state):
    from app.services import app_store_server_api as assa
    asked = []

    async def _fake(otid):
        asked.append(otid)
        return state
    monkeypatch.setattr(assa, "get_subscription_state", _fake)
    return asked


def _active(**over):
    state = {"entitled": True, "status": 1, "product_id": PRO, "bundle_id": BUNDLE,
             "environment": "Production", "original_transaction_id": REAL_OTID,
             "transaction_id": "2000001216894003", "price_paid": 14.99,
             "currency": "USD", "offer_type": None, "offer_discount_type": None,
             "auto_renew_status": 1}
    state.update(over)
    return state


def test_dial_on_an_unsigned_claim_apple_confirms_is_granted(
        client, free_user, tmp_db_path, verify_cfg, tier_changes, monkeypatch):
    asked = _apple_says(monkeypatch, _active())
    verify_cfg({"require_signed_transaction": True})

    r = _post(client, free_user, transaction_id=REAL_OTID)

    assert r.status_code == 200, r.text
    assert asked == [REAL_OTID]
    assert _tier(tmp_db_path, free_user["user_id"]) == "pro"
    events = _row(tmp_db_path,
                  "SELECT environment, original_transaction_id, transaction_id, price_paid "
                  "FROM subscription_events WHERE user_id = ?", (free_user["user_id"],))
    assert events == [{"environment": "Production", "original_transaction_id": REAL_OTID,
                       "transaction_id": "2000001216894003", "price_paid": 14.99}]


@pytest.mark.parametrize("state", [
    None,                                   # Apple has no record, or the API is not provisioned
    _active(entitled=False, status=2),      # real, but expired
    _active(product_id=PLUS),               # real, but not the product claimed
    _active(bundle_id="com.someone.else"),  # real, but another app's
    _active(bundle_id=None),                # Apple's signed transaction did not decode
], ids=["no-record", "expired", "other-product", "other-bundle", "no-bundle"])
def test_dial_on_an_unsigned_claim_apple_does_not_confirm_stays_a_400(
        client, free_user, tmp_db_path, verify_cfg, tier_changes, monkeypatch, state):
    _apple_says(monkeypatch, state)
    verify_cfg({"require_signed_transaction": True})

    r = _post(client, free_user, transaction_id=REAL_OTID)

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "receipt_unverified"
    assert _tier(tmp_db_path, free_user["user_id"]) == "free"


def test_a_present_but_invalid_signature_is_never_rescued_by_the_lookup(
        client, free_user, tmp_db_path, xcode, verify_cfg, tier_changes, monkeypatch):
    asked = _apple_says(monkeypatch, _active())
    verify_cfg({"require_signed_transaction": True})

    r = _post(client, free_user, _jws(xcode.key, [xcode.der], _payload()),
              transaction_id=REAL_OTID)

    assert r.status_code == 400, r.text
    assert asked == []
    assert _tier(tmp_db_path, free_user["user_id"]) == "free"


@pytest.mark.anyio
async def test_the_lookup_itself_refuses_a_request_that_carried_a_signature(monkeypatch):
    """The endpoint test above passes for two reasons at once: a refused
    signature leaves no id to look up, AND the lookup checks the request.
    This pins the second on its own, with an identity that does carry a
    plausible id, so the rule survives a change to the first."""
    asked = _apple_says(monkeypatch, _active())
    claim = rv.TransactionIdentity(
        original_transaction_id=REAL_OTID, transaction_id=REAL_OTID,
        environment=None, product_id=PRO, verified=False)

    signed = await rv.confirm_unsigned_with_apple(
        claim, _body("h.p.s", transaction_id=REAL_OTID), SETTINGS)
    assert signed is None
    assert asked == []

    # The positive control: the same claim with nothing signed is confirmed.
    unsigned = await rv.confirm_unsigned_with_apple(
        claim, _body(None, transaction_id=REAL_OTID), SETTINGS)
    assert unsigned is not None and unsigned.verified and unsigned.via_lookup
    assert asked == [REAL_OTID]


def test_dial_off_never_asks_apple(
        client, free_user, tmp_db_path, verify_cfg, tier_changes, monkeypatch):
    asked = _apple_says(monkeypatch, _active())
    verify_cfg({"require_signed_transaction": False})

    r = _post(client, free_user, transaction_id=REAL_OTID)

    assert r.status_code == 200, r.text
    assert asked == []


def test_a_lookup_never_takes_a_transaction_another_account_holds(
        client, free_user, pro_user, tmp_db_path, verify_cfg, tier_changes, monkeypatch):
    """Apple confirming the subscription is real says nothing about who
    is asking. Naming a paying customer's id must not buy their plan."""
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET original_transaction_id = ? WHERE id = ?",
                 (REAL_OTID, pro_user["user_id"]))
    conn.commit()
    conn.close()
    asked = _apple_says(monkeypatch, _active())
    verify_cfg({"require_signed_transaction": True})

    r = _post(client, free_user, transaction_id=REAL_OTID)

    assert r.status_code == 400, r.text
    assert asked == []
    users = {u["id"]: u for u in _row(
        tmp_db_path, "SELECT id, tier, original_transaction_id FROM users")}
    assert users[free_user["user_id"]]["tier"] == "free"
    assert users[pro_user["user_id"]]["tier"] == "pro"
    assert users[pro_user["user_id"]]["original_transaction_id"] == REAL_OTID


# --- the other door: sync-subscription ---------------------------------------
# It sets a tier from the client's word alone. With the dial on that word may
# keep or lower a plan and never raise one.

def _sync(client, user, product_id, is_trial=False):
    return client.post("/v1/sync-subscription",
                       json={"active_product_id": product_id, "is_trial": is_trial},
                       headers=user["headers"])


def test_dial_on_sync_subscription_never_raises_a_plan(
        client, free_user, tmp_db_path, verify_cfg, tier_changes):
    verify_cfg({"require_signed_transaction": True})

    r = _sync(client, free_user, PRO)

    assert r.status_code == 200, r.text
    assert r.json()["action"] == "none"
    assert r.json()["warning"] == "purchase_not_verified"
    assert _tier(tmp_db_path, free_user["user_id"]) == "free"


def test_dial_off_sync_subscription_behaves_as_before(
        client, free_user, tmp_db_path, verify_cfg, tier_changes):
    """Positive control: the same request does raise the plan with the dial off,
    so the test above is refused by the dial and not by a broken request."""
    verify_cfg({"require_signed_transaction": False})

    r = _sync(client, free_user, PRO)

    assert r.json()["action"] == "updated", r.text
    assert _tier(tmp_db_path, free_user["user_id"]) == "pro"


def test_dial_on_sync_subscription_still_lowers_a_plan(
        client, free_user, tmp_db_path, verify_cfg, tier_changes):
    verify_cfg({"require_signed_transaction": False})
    _sync(client, free_user, PRO)
    verify_cfg({"require_signed_transaction": True})

    r = _sync(client, free_user, None)

    assert r.json()["action"] == "downgraded", r.text
    assert _tier(tmp_db_path, free_user["user_id"]) == "free"
