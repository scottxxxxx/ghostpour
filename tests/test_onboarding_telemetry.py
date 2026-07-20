"""Onboarding funnel telemetry (2026-07-20). One `onboarding_completed`
event on the anonymous ping, routed to its own onboarding_events table,
keyed by device_id for the conversion join. Behavioral only, no PII.
See docs/wire-contracts/onboarding-telemetry.md."""

from __future__ import annotations

import json
import sqlite3
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _full_payload(**over):
    p = {
        "event_type": "onboarding_completed",
        "device_id": _uuid(),
        "app_version": "1.15",
        "distribution": "sandbox",
        "onboarding": {
            "total_duration_ms": 42000,
            "completed": True,
            "tour_skipped": False,
            "name_provided": True,
            "voice_enrolled": True,
            "auth_choice": "apple",
            "steps": [
                {"step": "welcome", "dwell_ms": 3000},
                {"step": "name_entry", "dwell_ms": 12000},
                {"step": "voice_enrollment", "dwell_ms": 20000},
            ],
        },
    }
    p.update(over)
    return p


def test_onboarding_event_persists_to_own_table(client, tmp_db_path):
    body = _full_payload()
    r = client.post("/v1/events/ping", json=body)
    assert r.status_code == 204
    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        """SELECT completed, name_provided, voice_enrolled, auth_choice,
                  total_duration_ms, steps, distribution
           FROM onboarding_events WHERE device_id = ?""",
        (body["device_id"],),
    ).fetchone()
    conn.close()
    assert row is not None
    completed, name_p, voice, auth, dur, steps_json, dist = row
    assert completed == 1 and name_p == 1 and voice == 1
    assert auth == "apple" and dur == 42000 and dist == "sandbox"
    steps = json.loads(steps_json)
    assert [s["step"] for s in steps] == ["welcome", "name_entry", "voice_enrollment"]
    assert steps[1]["dwell_ms"] == 12000


def test_abandoned_onboarding_persists_drop_step(client, tmp_db_path):
    body = _full_payload(onboarding={
        "total_duration_ms": 8000, "completed": False,
        "abandoned_at_step": "voice_enrollment",
        "steps": [{"step": "welcome", "dwell_ms": 3000},
                  {"step": "voice_enrollment", "dwell_ms": 5000}],
    })
    r = client.post("/v1/events/ping", json=body)
    assert r.status_code == 204
    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT completed, abandoned_at_step FROM onboarding_events WHERE device_id = ?",
        (body["device_id"],),
    ).fetchone()
    conn.close()
    assert row == (0, "voice_enrollment")


def test_onboarding_event_not_written_to_telemetry_events(client, tmp_db_path):
    body = _full_payload()
    client.post("/v1/events/ping", json=body)
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE device_id = ?",
        (body["device_id"],),
    ).fetchone()[0]
    conn.close()
    assert n == 0  # routed to its own table, not the lifecycle stream


def test_onboarding_completed_requires_payload(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "onboarding_completed", "device_id": _uuid(),
    })
    assert r.status_code == 422


def test_onboarding_payload_rejected_on_lifecycle_event(client):
    r = client.post("/v1/events/ping", json={
        "event_type": "app_start", "device_id": _uuid(),
        "onboarding": {"completed": True},
    })
    assert r.status_code == 422


def test_onboarding_still_enforces_uuid_device_id(client):
    r = client.post("/v1/events/ping", json=_full_payload(device_id="not-a-uuid"))
    assert r.status_code == 400


def test_lifecycle_ping_still_works(client, tmp_db_path):
    dev = _uuid()
    r = client.post("/v1/events/ping",
                    json={"event_type": "app_start", "device_id": dev})
    assert r.status_code == 204
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE device_id = ?", (dev,)
    ).fetchone()[0]
    conn.close()
    assert n == 1


# --- /admin/telemetry/onboarding dashboard endpoint ----------------------

ADMIN = {"X-Admin-Key": "test-admin-key"}


def test_onboarding_dashboard_shape_and_rates(client):
    # 2 completed, 1 abandoned
    client.post("/v1/events/ping", json=_full_payload())  # completed, apple
    client.post("/v1/events/ping", json=_full_payload(onboarding={
        "total_duration_ms": 30000, "completed": True, "name_provided": True,
        "voice_enrolled": False, "auth_choice": "on_device",
        "steps": [{"step": "highlights", "dwell_ms": 1000}]}))
    client.post("/v1/events/ping", json=_full_payload(onboarding={
        "total_duration_ms": 5000, "completed": False,
        "abandoned_at_step": "highlights",
        "steps": [{"step": "highlights", "dwell_ms": 5000}]}))
    d = client.get("/webhooks/admin/telemetry/onboarding?days=30", headers=ADMIN).json()
    for key in ("days", "kpis", "completion", "auth", "drop_off"):
        assert key in d
    k = d["kpis"]
    assert k["total"] == 3
    assert k["completed"] == 2 and k["abandoned"] == 1
    assert k["completion_rate"] == round(100 * 2 / 3, 1)
    # 2 of 3 report a name (the abandoned one defaulted name_provided false)
    assert k["name_provided_rate"] == round(100 * 2 / 3, 1)
    auth = {a["choice"]: a for a in d["auth"]}
    assert auth["apple"]["label"] == "Signed in with Apple"
    assert auth["on_device"]["label"] == "On-device (not signed in)"
    drop = {x["step"]: x["n"] for x in d["drop_off"]}
    assert drop.get("highlights") == 1  # only the abandoned one


def test_onboarding_dashboard_distribution_filter(client):
    client.post("/v1/events/ping", json=_full_payload(distribution="production"))
    client.post("/v1/events/ping", json=_full_payload(distribution="xcode"))
    prod = client.get(
        "/webhooks/admin/telemetry/onboarding?days=30&distribution=production",
        headers=ADMIN).json()
    assert prod["kpis"]["total"] == 1  # xcode excluded


def test_onboarding_dashboard_requires_admin(client):
    r = client.get("/webhooks/admin/telemetry/onboarding?days=30",
                   headers={"X-Admin-Key": "wrong-key"})
    assert r.status_code != 200
