"""Cert pin manifest signing service + public/admin endpoints.

Pins the contract iOS depends on:
- signature is over a canonical JSON form, and tampering breaks verification
- /v1/config/cert-pins returns the latest published manifest as JSON
- /admin/cert-pins/publish bumps the version monotonically
- /admin/cert-pins/current returns a verified=true round-trip when key matches
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings
from app.services import cert_pin_signing


def _fresh_key_b64() -> str:
    priv = Ed25519PrivateKey.generate()
    raw = priv.private_bytes_raw()
    return base64.b64encode(raw).decode("ascii")


def _settings_with_key(key_b64: str) -> Settings:
    # Build a Settings instance directly with required fields filled in.
    return Settings(
        jwt_secret="test-secret-key-that-is-long-enough-for-hs256-validation",
        cert_pin_signing_key_raw_b64=key_b64,
    )


def test_sign_and_verify_roundtrip():
    key_b64 = _fresh_key_b64()
    settings = _settings_with_key(key_b64)
    pub_b64 = cert_pin_signing.get_public_key_b64(settings)
    assert pub_b64

    from datetime import datetime, timezone, timedelta
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(days=60)

    manifest = cert_pin_signing.sign_manifest(
        settings,
        pins=["sha256/abc==", "sha256/def=="],
        version=1,
        issued_at=issued,
        expires_at=expires,
    )
    assert manifest["version"] == 1
    assert manifest["algorithm"] == "ed25519"
    assert manifest["pins"] == ["sha256/abc==", "sha256/def=="]
    assert cert_pin_signing.verify_signature(pub_b64, manifest) is True


def test_tampered_payload_fails_verification():
    key_b64 = _fresh_key_b64()
    settings = _settings_with_key(key_b64)
    pub_b64 = cert_pin_signing.get_public_key_b64(settings)

    from datetime import datetime, timezone, timedelta
    issued = datetime.now(timezone.utc)
    manifest = cert_pin_signing.sign_manifest(
        settings,
        pins=["sha256/abc=="],
        version=1,
        issued_at=issued,
        expires_at=issued + timedelta(days=60),
    )
    tampered = dict(manifest)
    tampered["pins"] = ["sha256/EVIL=="]
    assert cert_pin_signing.verify_signature(pub_b64, tampered) is False


def test_missing_key_raises():
    settings = _settings_with_key("")
    with pytest.raises(cert_pin_signing.CertPinSigningError):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cert_pin_signing.sign_manifest(
            settings, pins=["x"], version=1,
            issued_at=now, expires_at=now + timedelta(days=1),
        )


def test_malformed_key_raises():
    settings = _settings_with_key("not-base64!!!")
    # get_public_key_b64 tolerates a malformed key and returns None so
    # /admin/cert-pins/current can still report signing_configured=False.
    assert cert_pin_signing.get_public_key_b64(settings) is None
    # But sign_manifest WILL raise — exercise that path.
    with pytest.raises(cert_pin_signing.CertPinSigningError):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cert_pin_signing.sign_manifest(
            settings, pins=["x"], version=1,
            issued_at=now, expires_at=now + timedelta(days=1),
        )


def test_wrong_length_key_raises():
    too_short = base64.b64encode(b"too-short").decode("ascii")
    settings = _settings_with_key(too_short)
    with pytest.raises(cert_pin_signing.CertPinSigningError):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cert_pin_signing.sign_manifest(
            settings, pins=["x"], version=1,
            issued_at=now, expires_at=now + timedelta(days=1),
        )


# --- Endpoint tests (require app fixtures) ---------------------------------


@pytest.fixture
def app_env_with_pin_key(tmp_db_path):
    """Same as app_env but also provisions the cert pin signing key."""
    key_b64 = _fresh_key_b64()
    env = {
        "CZ_JWT_SECRET": "test-secret-key-that-is-long-enough-for-hs256-validation",
        "CZ_APPLE_BUNDLE_ID": "com.test.app",
        "CZ_ADMIN_KEY": "test-admin-key",
        "CZ_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_db_path}",
        "CZ_CERT_PIN_SIGNING_KEY_RAW_B64": key_b64,
    }
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    from app.config import get_settings
    get_settings.cache_clear()
    yield {"key_b64": key_b64, "env": env}
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


@pytest.fixture
def client_with_pins(app_env_with_pin_key, mock_provider, mock_pricing):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_public_endpoint_404_when_no_manifest(client_with_pins):
    resp = client_with_pins.get("/v1/config/cert-pins")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "no_manifest"


def test_admin_publish_then_public_get(client_with_pins):
    resp = client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": ["sha256/AAA==", "sha256/BBB=="], "days_valid": 30},
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert resp.status_code == 200, resp.text
    manifest = resp.json()
    assert manifest["version"] == 1
    assert manifest["pins"] == ["sha256/AAA==", "sha256/BBB=="]

    pub_resp = client_with_pins.get("/v1/config/cert-pins")
    assert pub_resp.status_code == 200
    served = pub_resp.json()
    assert served["version"] == 1
    assert served["signature"] == manifest["signature"]
    assert pub_resp.headers["cache-control"].startswith("public")


def test_admin_publish_bumps_version(client_with_pins):
    headers = {"X-Admin-Key": "test-admin-key"}
    r1 = client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": ["sha256/v1=="]}, headers=headers,
    )
    r2 = client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": ["sha256/v2=="]}, headers=headers,
    )
    assert r1.json()["version"] == 1
    assert r2.json()["version"] == 2

    served = client_with_pins.get("/v1/config/cert-pins").json()
    assert served["version"] == 2
    assert served["pins"] == ["sha256/v2=="]


def test_admin_current_round_trip_verifies(client_with_pins):
    headers = {"X-Admin-Key": "test-admin-key"}
    client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": ["sha256/X=="]}, headers=headers,
    )
    cur = client_with_pins.get(
        "/webhooks/admin/cert-pins/current", headers=headers,
    ).json()
    assert cur["signing_configured"] is True
    assert cur["public_key_b64"]
    assert cur["manifest"]["version"] == 1
    assert cur["verified"] is True


def test_admin_publish_requires_admin_key(client_with_pins):
    resp = client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": ["sha256/X=="]},
        headers={"X-Admin-Key": "wrong-key"},
    )
    assert resp.status_code == 403


def test_admin_publish_empty_pins_rejected(client_with_pins):
    resp = client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": []},
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert resp.status_code == 400
