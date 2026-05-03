"""Tests for the Email Management admin endpoints."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


_KEY = {"X-Admin-Key": "test-admin-key"}


def _seed_event(
    db_path: str,
    *,
    event_id: str,
    event_type: str,
    recipient: str | None = None,
    email_id: str | None = None,
    bounce_type: str | None = None,
    received_at: str | None = None,
) -> None:
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"type": event_type, "data": {"to": [recipient] if recipient else None}})
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO email_events (id, event_type, recipient, email_id, bounce_type, payload, received_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, event_type, recipient, email_id, bounce_type, payload, received_at),
    )
    conn.commit()
    conn.close()


def _seed_suppression(db_path: str, *, recipient: str, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO email_suppression (recipient, reason, source_event_id, suppressed_at)"
        " VALUES (?, ?, ?, ?)",
        (recipient.lower(), reason, "msg_test_xx", now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_missing_admin_key_blocked(client):
    for path in (
        "/webhooks/admin/email/stats",
        "/webhooks/admin/email/events",
        "/webhooks/admin/email/suppression",
    ):
        resp = client.get(path)
        assert resp.status_code in (401, 422), f"{path} should require admin key"


def test_wrong_admin_key_blocked(client):
    resp = client.get("/webhooks/admin/email/stats", headers={"X-Admin-Key": "wrong"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_stats_empty_dataset(client):
    resp = client.get("/webhooks/admin/email/stats", headers=_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] == 0
    assert body["by_type"] == {}
    assert body["suppression_count"] == 0
    assert body["hard_bounces"] == 0
    assert body["complaints"] == 0
    # Webhook health (added in M2 — visibility for silent 401 storms)
    assert "webhook" in body
    assert "signing_secret_configured" in body["webhook"]
    assert "last_event_received_at" in body["webhook"]
    assert body["webhook"]["last_event_received_at"] is None


def test_stats_webhook_last_event_reflects_recent_seed(client, tmp_db_path):
    _seed_event(tmp_db_path, event_id="e_recent", event_type="email.delivered",
                recipient="x@example.com")
    resp = client.get("/webhooks/admin/email/stats", headers=_KEY)
    body = resp.json()
    assert body["webhook"]["last_event_received_at"] is not None


def test_stats_signing_secret_reflects_env(client, monkeypatch):
    """webhook.signing_secret_configured is True when CZ_RESEND_WEBHOOK_SECRET
    is set, False when unset (and SM also returns empty)."""
    from app import secrets as app_secrets
    monkeypatch.setenv("CZ_RESEND_WEBHOOK_SECRET", "whsec_test_dummy")
    app_secrets.get_secret.cache_clear()
    resp = client.get("/webhooks/admin/email/stats", headers=_KEY)
    assert resp.json()["webhook"]["signing_secret_configured"] is True

    monkeypatch.delenv("CZ_RESEND_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(app_secrets, "_from_secret_manager", lambda name: "")
    app_secrets.get_secret.cache_clear()
    resp = client.get("/webhooks/admin/email/stats", headers=_KEY)
    assert resp.json()["webhook"]["signing_secret_configured"] is False
    app_secrets.get_secret.cache_clear()


def test_stats_counts_events_correctly(client, tmp_db_path):
    _seed_event(tmp_db_path, event_id="e1", event_type="email.delivered", recipient="a@x.com")
    _seed_event(tmp_db_path, event_id="e2", event_type="email.delivered", recipient="b@x.com")
    _seed_event(
        tmp_db_path, event_id="e3", event_type="email.bounced",
        recipient="c@x.com", bounce_type="hard",
    )
    _seed_event(
        tmp_db_path, event_id="e4", event_type="email.bounced",
        recipient="d@x.com", bounce_type="soft",
    )
    _seed_event(tmp_db_path, event_id="e5", event_type="email.complained", recipient="c@x.com")
    _seed_suppression(tmp_db_path, recipient="c@x.com", reason="hard_bounce")

    resp = client.get("/webhooks/admin/email/stats", headers=_KEY)
    body = resp.json()
    assert body["total_events"] == 5
    assert body["by_type"] == {
        "email.delivered": 2,
        "email.bounced": 2,
        "email.complained": 1,
    }
    assert body["hard_bounces"] == 1   # only the hard one, not the soft
    assert body["complaints"] == 1
    assert body["suppression_count"] == 1
    assert body["suppression_by_reason"] == {"hard_bounce": 1}


# ---------------------------------------------------------------------------
# Events list
# ---------------------------------------------------------------------------

def test_events_list_returns_seeded_rows(client, tmp_db_path):
    _seed_event(tmp_db_path, event_id="e_a", event_type="email.sent", recipient="a@x.com")
    _seed_event(tmp_db_path, event_id="e_b", event_type="email.delivered", recipient="b@x.com")

    resp = client.get("/webhooks/admin/email/events", headers=_KEY)
    body = resp.json()
    assert body["total"] == 2
    assert len(body["events"]) == 2
    ids = {e["id"] for e in body["events"]}
    assert ids == {"e_a", "e_b"}


def test_events_list_filters_by_type(client, tmp_db_path):
    _seed_event(tmp_db_path, event_id="ev1", event_type="email.delivered", recipient="a@x.com")
    _seed_event(
        tmp_db_path, event_id="ev2", event_type="email.bounced",
        recipient="b@x.com", bounce_type="hard",
    )

    resp = client.get(
        "/webhooks/admin/email/events?event_type=email.bounced", headers=_KEY,
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["event_type"] == "email.bounced"
    assert body["events"][0]["bounce_type"] == "hard"


def test_events_list_filters_by_recipient_case_insensitive(client, tmp_db_path):
    _seed_event(tmp_db_path, event_id="ev1", event_type="email.delivered", recipient="picky@x.com")
    _seed_event(tmp_db_path, event_id="ev2", event_type="email.delivered", recipient="other@x.com")

    resp = client.get(
        "/webhooks/admin/email/events?recipient=PICKY@X.COM", headers=_KEY,
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["recipient"] == "picky@x.com"


def test_events_list_pagination(client, tmp_db_path):
    for i in range(5):
        _seed_event(
            tmp_db_path, event_id=f"page_{i}", event_type="email.delivered",
            recipient=f"u{i}@x.com",
        )

    resp = client.get("/webhooks/admin/email/events?limit=2&offset=0", headers=_KEY)
    body = resp.json()
    assert body["total"] == 5
    assert len(body["events"]) == 2

    resp2 = client.get("/webhooks/admin/email/events?limit=2&offset=4", headers=_KEY)
    body2 = resp2.json()
    assert body2["total"] == 5
    assert len(body2["events"]) == 1


# ---------------------------------------------------------------------------
# Suppression list
# ---------------------------------------------------------------------------

def test_suppression_list_returns_active_rows(client, tmp_db_path):
    _seed_suppression(tmp_db_path, recipient="bounced@x.com", reason="hard_bounce")
    _seed_suppression(tmp_db_path, recipient="spammer@x.com", reason="spam_complaint")

    resp = client.get("/webhooks/admin/email/suppression", headers=_KEY)
    body = resp.json()
    assert body["total"] == 2
    by_recipient = {row["recipient"]: row["reason"] for row in body["suppression"]}
    assert by_recipient == {
        "bounced@x.com": "hard_bounce",
        "spammer@x.com": "spam_complaint",
    }


def test_suppression_list_pagination(client, tmp_db_path):
    for i in range(3):
        _seed_suppression(tmp_db_path, recipient=f"x{i}@x.com", reason="hard_bounce")

    resp = client.get("/webhooks/admin/email/suppression?limit=2&offset=0", headers=_KEY)
    body = resp.json()
    assert body["total"] == 3
    assert len(body["suppression"]) == 2
