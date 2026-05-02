"""Tests for the Resend webhook ingestion endpoint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app import secrets as app_secrets


_TEST_SECRET = "whsec_dGVzdHNlY3JldHRlc3RzZWNyZXR0ZXN0c2VjcmV0"


def _sign(secret: str, msg_id: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    """Construct Svix-style headers for a signed test payload."""
    if timestamp is None:
        timestamp = int(time.time())
    ts = str(timestamp)
    secret_bytes = base64.b64decode(secret.removeprefix("whsec_"))
    content = f"{msg_id}.{ts}.{body.decode('utf-8')}".encode()
    sig = base64.b64encode(hmac.new(secret_bytes, content, hashlib.sha256).digest()).decode()
    return {
        "svix-id": msg_id,
        "svix-timestamp": ts,
        "svix-signature": f"v1,{sig}",
    }


def _payload(event_type: str, **data) -> dict:
    """Build a Resend webhook event body."""
    return {
        "type": event_type,
        "created_at": "2026-05-02T20:00:00.000Z",
        "data": data,
    }


@pytest.fixture
def webhook_secret(monkeypatch) -> Generator[str, None, None]:
    monkeypatch.setenv("CZ_RESEND_WEBHOOK_SECRET", _TEST_SECRET)
    app_secrets.get_secret.cache_clear()
    yield _TEST_SECRET
    app_secrets.get_secret.cache_clear()


def _post(client: TestClient, body: dict, secret: str, *, msg_id: str | None = None) -> dict:
    msg_id = msg_id or f"msg_{uuid.uuid4().hex[:16]}"
    raw = json.dumps(body).encode()
    headers = _sign(secret, msg_id, raw)
    resp = client.post("/webhooks/resend", content=raw, headers=headers)
    return {"resp": resp, "msg_id": msg_id, "raw": raw}


# ---------------------------------------------------------------------------
# Signature + transport
# ---------------------------------------------------------------------------

def test_invalid_signature_returns_401(client, webhook_secret):
    body = _payload("email.delivered", to=["x@example.com"], email_id="em_1")
    raw = json.dumps(body).encode()
    headers = _sign(webhook_secret, "msg_1", raw)
    headers["svix-signature"] = "v1,deadbeef"  # tamper
    resp = client.post("/webhooks/resend", content=raw, headers=headers)
    assert resp.status_code == 401


def test_missing_svix_id_returns_4xx(client, webhook_secret):
    body = _payload("email.delivered", to=["x@example.com"])
    raw = json.dumps(body).encode()
    headers = _sign(webhook_secret, "msg_2", raw)
    headers.pop("svix-id")
    resp = client.post("/webhooks/resend", content=raw, headers=headers)
    # Svix raises on missing headers — surface as 401 from verifier
    assert resp.status_code in (400, 401)


def test_unconfigured_secret_returns_503(client, monkeypatch):
    """When the webhook secret is missing, ingest returns 503 (fail-closed)."""
    monkeypatch.delenv("CZ_RESEND_WEBHOOK_SECRET", raising=False)
    app_secrets.get_secret.cache_clear()
    # Force secret manager to also return empty
    monkeypatch.setattr(app_secrets, "_from_secret_manager", lambda name: "")
    resp = client.post("/webhooks/resend", content=b"{}", headers={"svix-id": "x"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Bounce handling
# ---------------------------------------------------------------------------

def test_hard_bounce_creates_suppression(client, webhook_secret, tmp_db_path):
    body = _payload(
        "email.bounced",
        to=["bounced@example.com"],
        email_id="em_bounce_1",
        bounce={"type": "hard", "subType": "general"},
    )
    result = _post(client, body, webhook_secret)
    assert result["resp"].status_code == 200, result["resp"].text

    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT recipient, reason, source_event_id FROM email_suppression"
    ).fetchone()
    conn.close()
    assert row == ("bounced@example.com", "hard_bounce", result["msg_id"])


def test_soft_bounce_does_not_suppress(client, webhook_secret, tmp_db_path):
    body = _payload(
        "email.bounced",
        to=["soft@example.com"],
        email_id="em_bounce_2",
        bounce={"type": "soft"},
    )
    result = _post(client, body, webhook_secret)
    assert result["resp"].status_code == 200

    conn = sqlite3.connect(tmp_db_path)
    suppression_count = conn.execute(
        "SELECT COUNT(*) FROM email_suppression"
    ).fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM email_events").fetchone()[0]
    conn.close()
    assert suppression_count == 0
    assert event_count == 1  # event still recorded


# ---------------------------------------------------------------------------
# Complaint handling
# ---------------------------------------------------------------------------

def test_complaint_creates_suppression(client, webhook_secret, tmp_db_path):
    body = _payload(
        "email.complained",
        to=["complained@example.com"],
        email_id="em_complaint_1",
    )
    result = _post(client, body, webhook_secret)
    assert result["resp"].status_code == 200

    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT recipient, reason FROM email_suppression"
    ).fetchone()
    conn.close()
    assert row == ("complained@example.com", "spam_complaint")


# ---------------------------------------------------------------------------
# Lifecycle events (audit-only)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event_type", ["email.delivered", "email.sent", "email.delivery_delayed"])
def test_lifecycle_events_log_only(client, webhook_secret, tmp_db_path, event_type):
    body = _payload(event_type, to=["lifecycle@example.com"], email_id="em_lc_1")
    result = _post(client, body, webhook_secret)
    assert result["resp"].status_code == 200

    conn = sqlite3.connect(tmp_db_path)
    suppression_count = conn.execute(
        "SELECT COUNT(*) FROM email_suppression"
    ).fetchone()[0]
    event_row = conn.execute(
        "SELECT event_type, recipient FROM email_events"
    ).fetchone()
    conn.close()
    assert suppression_count == 0
    assert event_row == (event_type, "lifecycle@example.com")


# ---------------------------------------------------------------------------
# Unknown events + idempotency
# ---------------------------------------------------------------------------

def test_unknown_event_type_returns_200(client, webhook_secret, tmp_db_path):
    body = _payload("email.something_new_we_havent_heard_of", to=["x@example.com"])
    result = _post(client, body, webhook_secret)
    assert result["resp"].status_code == 200

    conn = sqlite3.connect(tmp_db_path)
    event_row = conn.execute(
        "SELECT event_type FROM email_events"
    ).fetchone()
    conn.close()
    assert event_row == ("email.something_new_we_havent_heard_of",)


def test_duplicate_svix_id_is_idempotent(client, webhook_secret, tmp_db_path):
    body = _payload(
        "email.bounced",
        to=["dupe@example.com"],
        email_id="em_dupe_1",
        bounce={"type": "hard"},
    )
    msg_id = "msg_fixed_for_dedup_test"
    first = _post(client, body, webhook_secret, msg_id=msg_id)
    second = _post(client, body, webhook_secret, msg_id=msg_id)

    assert first["resp"].status_code == 200
    assert second["resp"].status_code == 200
    assert second["resp"].json().get("duplicate") is True

    conn = sqlite3.connect(tmp_db_path)
    event_count = conn.execute("SELECT COUNT(*) FROM email_events").fetchone()[0]
    suppression_count = conn.execute(
        "SELECT COUNT(*) FROM email_suppression"
    ).fetchone()[0]
    conn.close()
    assert event_count == 1
    assert suppression_count == 1


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_recipient_email_is_normalized_to_lowercase(client, webhook_secret, tmp_db_path):
    body = _payload(
        "email.bounced",
        to=["MixedCase@Example.COM"],
        email_id="em_case_1",
        bounce={"type": "hard"},
    )
    result = _post(client, body, webhook_secret)
    assert result["resp"].status_code == 200

    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT recipient FROM email_suppression"
    ).fetchone()
    conn.close()
    assert row == ("mixedcase@example.com",)


# ---------------------------------------------------------------------------
# Suppression service (direct unit tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_suppressed_handles_case_insensitively(client, webhook_secret, tmp_db_path):
    """End-to-end: hard-bounce a mixed-case address, then verify is_suppressed
    returns True for any casing."""
    body = _payload(
        "email.bounced",
        to=["Caser@Example.com"],
        email_id="em_case_2",
        bounce={"type": "hard"},
    )
    _post(client, body, webhook_secret)

    import aiosqlite
    from app.services.email_suppression import is_suppressed

    async with aiosqlite.connect(tmp_db_path) as db:
        assert await is_suppressed(db, "caser@example.com") is True
        assert await is_suppressed(db, "CASER@EXAMPLE.COM") is True
        assert await is_suppressed(db, "  Caser@Example.com  ") is True
        assert await is_suppressed(db, "other@example.com") is False
