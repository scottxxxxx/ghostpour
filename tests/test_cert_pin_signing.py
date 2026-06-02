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


# --- Auto-republish + status banner ---------------------------------------

import pytest_asyncio
from unittest.mock import patch
from datetime import datetime, timezone, timedelta


def test_compute_status_no_signing_returns_red():
    from app.services.cert_pin_auto_republish import compute_status
    s = compute_status(signing_configured=False, current=None, last_check=None)
    assert s["level"] == "red"
    assert "not configured" in s["text"].lower()


def test_compute_status_no_manifest_returns_red():
    from app.services.cert_pin_auto_republish import compute_status
    s = compute_status(signing_configured=True, current=None, last_check=None)
    assert s["level"] == "red"
    assert "no cert pin manifest" in s["text"].lower()


def test_compute_status_healthy_returns_green():
    from app.services.cert_pin_auto_republish import compute_status
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    current = {
        "version": 1,
        "expires_at": (now + timedelta(days=60)).isoformat().replace("+00:00", "Z"),
        "pins": ["sha256/AAA=="],
    }
    s = compute_status(signing_configured=True, current=current, last_check=None, now=now)
    assert s["level"] == "green"
    assert s["version"] == 1
    assert 59.9 < s["days_remaining"] < 60.1


def test_compute_status_warn_band_returns_yellow():
    from app.services.cert_pin_auto_republish import compute_status
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    current = {
        "version": 1,
        "expires_at": (now + timedelta(days=10)).isoformat().replace("+00:00", "Z"),
        "pins": ["sha256/AAA=="],
    }
    s = compute_status(signing_configured=True, current=current, last_check=None, now=now)
    assert s["level"] == "yellow"


def test_compute_status_auto_window_returns_yellow():
    from app.services.cert_pin_auto_republish import compute_status
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    current = {
        "version": 1,
        "expires_at": (now + timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        "pins": ["sha256/AAA=="],
    }
    s = compute_status(signing_configured=True, current=current, last_check=None, now=now)
    assert s["level"] == "yellow"
    assert "auto-republish" in s["text"].lower()


def test_compute_status_expired_returns_red():
    from app.services.cert_pin_auto_republish import compute_status
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    current = {
        "version": 1,
        "expires_at": (now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        "pins": ["sha256/AAA=="],
    }
    s = compute_status(signing_configured=True, current=current, last_check=None, now=now)
    assert s["level"] == "red"
    assert "expired" in s["text"].lower()


def test_compute_status_last_failed_returns_red():
    from app.services.cert_pin_auto_republish import compute_status, CheckResult
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    current = {
        "version": 1,
        "expires_at": (now + timedelta(days=60)).isoformat().replace("+00:00", "Z"),
        "pins": ["sha256/AAA=="],
    }
    last = CheckResult(
        checked_at=now,
        action="failed",
        version_after=1,
        detail="chain fetch failed: timeout",
    )
    s = compute_status(signing_configured=True, current=current, last_check=last, now=now)
    assert s["level"] == "red"
    assert "failed" in s["text"].lower()


@pytest.mark.asyncio
async def test_maybe_auto_republish_no_signing(tmp_path):
    """No signing key configured = noop_no_signing, no alert, no error."""
    from app.services.cert_pin_auto_republish import maybe_auto_republish
    import aiosqlite
    from app.database import MIGRATIONS

    db_path = str(tmp_path / "test.db")
    from app.database import init_db
    await init_db(f"sqlite+aiosqlite:///{db_path}")
    async with aiosqlite.connect(db_path) as db:
        settings = _settings_with_key("")
        result = await maybe_auto_republish(db, settings)
    assert result.action == "noop_no_signing"


@pytest.mark.asyncio
async def test_maybe_auto_republish_healthy_is_noop(tmp_path):
    """Manifest with plenty of time remaining = noop_healthy."""
    from app.services.cert_pin_auto_republish import maybe_auto_republish
    from app.services.cert_pin_signing import publish_manifest
    import aiosqlite
    from app.database import MIGRATIONS

    db_path = str(tmp_path / "test.db")
    from app.database import init_db
    await init_db(f"sqlite+aiosqlite:///{db_path}")
    async with aiosqlite.connect(db_path) as db:
        settings = _settings_with_key(_fresh_key_b64())
        await publish_manifest(
            db, settings, pins=["sha256/AAA=="], days_valid=60,
        )
        result = await maybe_auto_republish(db, settings)
    assert result.action == "noop_healthy"
    assert result.version_after == 1


@pytest.mark.asyncio
async def test_maybe_auto_republish_in_window_publishes(tmp_path):
    """Manifest expiring in <=7 days = publishes v2."""
    from app.services.cert_pin_auto_republish import maybe_auto_republish
    from app.services.cert_pin_signing import publish_manifest
    import aiosqlite
    from app.database import MIGRATIONS

    db_path = str(tmp_path / "test.db")
    from app.database import init_db
    await init_db(f"sqlite+aiosqlite:///{db_path}")
    async with aiosqlite.connect(db_path) as db:
        settings = _settings_with_key(_fresh_key_b64())
        # Publish a manifest that expires in 3 days.
        await publish_manifest(
            db, settings, pins=["sha256/OLD=="], days_valid=3,
        )
        # Stub the live chain fetch to return a known pin set.
        with patch(
            "app.services.cert_pin_auto_republish.fetch_current_chain_pins",
            return_value=["sha256/OLD=="],
        ):
            result = await maybe_auto_republish(db, settings)
    assert result.action == "republished"
    assert result.version_after == 2


@pytest.mark.asyncio
async def test_maybe_auto_republish_fires_alert_on_pin_change(tmp_path):
    """When live chain pins differ from prior publish, an incident fires."""
    from app.services.cert_pin_auto_republish import maybe_auto_republish
    from app.services.cert_pin_signing import publish_manifest
    import aiosqlite
    from app.database import MIGRATIONS

    db_path = str(tmp_path / "test.db")
    from app.database import init_db
    await init_db(f"sqlite+aiosqlite:///{db_path}")
    async with aiosqlite.connect(db_path) as db:
        settings = _settings_with_key(_fresh_key_b64())
        await publish_manifest(
            db, settings, pins=["sha256/OLD=="], days_valid=3,
        )

        called = {}

        async def _stub_report_incident(db_arg, **kwargs):
            called["category"] = kwargs.get("category")
            called["subject"] = kwargs.get("subject")
            called["details"] = kwargs.get("details")
            class _R:
                incident_id = "test-id"
                is_new = True
                emailed_to: list[str] = []
                suppressed_reason = None
            return _R()

        with patch(
            "app.services.cert_pin_auto_republish.fetch_current_chain_pins",
            return_value=["sha256/NEW1==", "sha256/NEW2=="],
        ), patch(
            "app.services.alerting.report_incident",
            new=_stub_report_incident,
        ):
            result = await maybe_auto_republish(db, settings)
    assert result.action == "republished"
    assert called.get("category") == "cert_pin_auto_republish"
    assert called.get("subject") == "pin_set_changed"


def test_fetch_current_chain_pins_against_prod():
    """Integration test against real prod chain. Skipped when network
    is unavailable. Confirms we return exactly 3 pins (intermediate +
    both roots) skipping the leaf."""
    import socket
    from app.services.cert_pin_signing import fetch_current_chain_pins
    try:
        socket.create_connection(("cz.shouldersurf.com", 443), timeout=5).close()
    except OSError:
        pytest.skip("no network or prod unreachable")
    pins = fetch_current_chain_pins("cz.shouldersurf.com")
    assert len(pins) == 3
    assert all(p.startswith("sha256/") for p in pins)


def test_admin_status_endpoint_returns_banner_data(client_with_pins):
    """Status endpoint surfaces compute_status output with admin auth."""
    headers = {"X-Admin-Key": "test-admin-key"}
    # Before any publish, expect red+no manifest.
    resp = client_with_pins.get(
        "/webhooks/admin/cert-pins/status", headers=headers,
    )
    assert resp.status_code == 200
    s = resp.json()
    assert s["level"] == "red"
    assert s["version"] is None

    # After publish, expect green.
    client_with_pins.post(
        "/webhooks/admin/cert-pins/publish",
        json={"pins": ["sha256/AAA=="], "days_valid": 60},
        headers=headers,
    )
    s2 = client_with_pins.get(
        "/webhooks/admin/cert-pins/status", headers=headers,
    ).json()
    assert s2["level"] == "green"
    assert s2["version"] == 1
