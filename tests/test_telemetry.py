"""Tests for the anonymous telemetry endpoint + daily rollup service.

Endpoint shape and rollup math are covered here. The dashboard JS that
consumes /admin/telemetry/summary is verified by hitting the endpoint
and asserting the shape it relies on.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite
import pytest


_VALID_UUID = "12345678-1234-1234-1234-123456789012"
_ADMIN_KEY = "test-admin-key"


# --- POST /v1/events/ping --------------------------------------------------


def test_ping_accepts_app_start(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "app_start",
        "device_id": _VALID_UUID,
    })
    assert r.status_code == 204


def test_ping_accepts_meeting_start_with_model(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "meeting_start",
        "device_id": _VALID_UUID,
        "model_id": "claude-sonnet-4-6",
        "meeting_id": str(uuid.uuid4()),
    })
    assert r.status_code == 204


def test_ping_accepts_meeting_stop_with_duration(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "meeting_stop",
        "device_id": _VALID_UUID,
        "meeting_id": str(uuid.uuid4()),
        "duration_seconds": 1845,
    })
    assert r.status_code == 204


def test_ping_rejects_malformed_device_id(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "app_start",
        "device_id": "not-a-uuid",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_request"


def test_ping_rejects_unknown_event_type(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "user_logged_in",
        "device_id": _VALID_UUID,
    })
    # Pydantic Literal -> 422 unprocessable
    assert r.status_code == 422


def test_ping_rejects_missing_required_fields(client):
    r = client.post("/v1/events/ping", json={"event_type": "app_start"})
    assert r.status_code == 422


def test_ping_persists_user_id_when_provided(client, tmp_db_path):
    user_id = str(uuid.uuid4())
    r = client.post("/v1/events/ping", json={
        "event_type": "app_start",
        "device_id": _VALID_UUID,
        "user_id": user_id,
    })
    assert r.status_code == 204

    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT user_id, device_id FROM telemetry_events WHERE device_id = ?",
        (_VALID_UUID,),
    ).fetchone()
    assert row == (user_id, _VALID_UUID)


def test_ping_persists_ip_hash_not_raw(client, tmp_db_path):
    r = client.post("/v1/events/ping", json={
        "event_type": "app_start",
        "device_id": _VALID_UUID,
    })
    assert r.status_code == 204

    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT ip_hash FROM telemetry_events WHERE device_id = ?",
        (_VALID_UUID,),
    ).fetchone()
    ip_hash = row[0] if row else None
    # Either empty (no client in test scope) or a 64-char SHA-256 hex.
    # Either way, raw IPs like "127.0.0.1" must NOT be stored.
    assert ip_hash is None or ip_hash == "" or (len(ip_hash) == 64 and all(c in "0123456789abcdef" for c in ip_hash))
    assert ip_hash not in ("127.0.0.1", "testclient", "localhost")


def test_ping_no_auth_required(client):
    """Endpoint must work pre-login (no Authorization header)."""
    r = client.post("/v1/events/ping", json={
        "event_type": "app_start",
        "device_id": _VALID_UUID,
    })
    assert r.status_code == 204


def test_ping_rate_limit_kicks_in(client):
    """61 rapid pings from the same hashed IP should yield a 429 on the last one.
    Uses the in-memory RateLimiter; per-IP cap is 60/min."""
    body = {"event_type": "app_start", "device_id": _VALID_UUID}
    # Force a non-empty X-Forwarded-For so _client_ip yields a stable bucket
    # even when TestClient leaves request.client empty.
    headers = {"x-forwarded-for": "203.0.113.10"}
    last_status = None
    for _ in range(61):
        last_status = client.post("/v1/events/ping", json=body, headers=headers).status_code
    assert last_status == 429


# --- Rollup math -----------------------------------------------------------


def _seed_events(tmp_db_path: str, events: list[dict]) -> None:
    """Insert raw telemetry rows directly for rollup testing."""
    conn = sqlite3.connect(tmp_db_path)
    for ev in events:
        conn.execute(
            """INSERT INTO telemetry_events
               (id, event_type, device_id, user_id, meeting_id, model_id,
                app_version, os_version, duration_seconds, ip_hash, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                ev["event_type"],
                ev["device_id"],
                ev.get("user_id"),
                ev.get("meeting_id"),
                ev.get("model_id"),
                ev.get("app_version"),
                ev.get("os_version"),
                ev.get("duration_seconds"),
                ev.get("ip_hash", "h"),
                ev["received_at"],
            ),
        )
    conn.commit()
    conn.close()


def test_rollup_counts_events_per_day(client, tmp_db_path):
    from app.services.telemetry_rollup import compute_rollups

    today_iso = datetime.now(timezone.utc).date().isoformat()
    received_at = f"{today_iso}T12:00:00+00:00"
    _seed_events(tmp_db_path, [
        {"event_type": "app_start",     "device_id": "d1", "received_at": received_at},
        {"event_type": "app_start",     "device_id": "d2", "received_at": received_at},
        {"event_type": "meeting_start", "device_id": "d1", "model_id": "haiku", "received_at": received_at},
        {"event_type": "meeting_stop",  "device_id": "d1", "model_id": "haiku", "duration_seconds": 300, "received_at": received_at},
        {"event_type": "meeting_stop",  "device_id": "d2", "model_id": "sonnet", "duration_seconds": 600, "received_at": received_at},
    ])

    async def run():
        async with aiosqlite.connect(tmp_db_path) as db:
            await compute_rollups(db)

    asyncio.run(run())

    conn = sqlite3.connect(tmp_db_path)
    rows = {
        m: v for m, v in conn.execute(
            "SELECT metric, value FROM telemetry_daily_rollups WHERE day = ?",
            (today_iso,),
        ).fetchall()
    }
    assert rows["app_starts"] == 2
    assert rows["meetings_started"] == 1
    assert rows["meetings_stopped"] == 2
    assert rows["distinct_devices"] == 2
    assert rows["meetings_per_model:haiku"] == 2  # start + stop
    assert rows["meetings_per_model:sonnet"] == 1
    # avg = (300 + 600) / 2 = 450
    assert rows["duration_avg_sec"] == 450.0
    assert rows["duration_min_sec"] == 300.0
    assert rows["duration_max_sec"] == 600.0


def test_rollup_is_idempotent(client, tmp_db_path):
    """Re-running rollup must overwrite, not duplicate, per (day, metric)."""
    from app.services.telemetry_rollup import compute_rollups

    today_iso = datetime.now(timezone.utc).date().isoformat()
    _seed_events(tmp_db_path, [
        {"event_type": "app_start", "device_id": "d1",
         "received_at": f"{today_iso}T12:00:00+00:00"},
    ])

    async def run():
        async with aiosqlite.connect(tmp_db_path) as db:
            await compute_rollups(db)
            await compute_rollups(db)

    asyncio.run(run())

    conn = sqlite3.connect(tmp_db_path)
    rows = conn.execute(
        "SELECT COUNT(*) FROM telemetry_daily_rollups WHERE day = ? AND metric = 'app_starts'",
        (today_iso,),
    ).fetchone()
    assert rows[0] == 1


# --- GET /admin/telemetry/summary ------------------------------------------


def test_admin_summary_rejects_missing_admin_key(client):
    r = client.get("/webhooks/admin/telemetry/summary")
    assert r.status_code == 422  # Header(...) missing


def test_admin_summary_returns_expected_shape(client, tmp_db_path):
    from app.services.telemetry_rollup import compute_rollups

    today_iso = datetime.now(timezone.utc).date().isoformat()
    _seed_events(tmp_db_path, [
        {"event_type": "app_start",     "device_id": "d1", "received_at": f"{today_iso}T12:00:00+00:00"},
        {"event_type": "meeting_start", "device_id": "d1", "model_id": "haiku",
         "received_at": f"{today_iso}T12:00:00+00:00"},
        {"event_type": "meeting_stop",  "device_id": "d1", "model_id": "haiku", "duration_seconds": 120,
         "received_at": f"{today_iso}T12:00:00+00:00"},
    ])

    async def run():
        async with aiosqlite.connect(tmp_db_path) as db:
            await compute_rollups(db)
    asyncio.run(run())

    r = client.get("/webhooks/admin/telemetry/summary?days=30",
                   headers={"X-Admin-Key": _ADMIN_KEY})
    assert r.status_code == 200
    data = r.json()
    assert data["days"] == 30
    assert set(data["series"].keys()) == {
        "app_starts", "meetings_started", "meetings_stopped",
        "distinct_devices", "distinct_users",
    }
    assert isinstance(data["models"], list)
    assert any(m["model_id"] == "haiku" for m in data["models"])
    assert data["duration"]["avg_sec"] == 120.0
    assert data["duration"]["sample_size"] == 1
