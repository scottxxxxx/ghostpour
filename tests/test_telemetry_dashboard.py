"""Telemetry dashboard tests.

Covers:
- device_models mapping (known + unknown + None)
- /v1/events/ping accepts new device_model + app_locale fields
- backward compat: old payloads without the new fields still validate
- /admin/telemetry/rich shape with no filters
- /admin/telemetry/rich applies each filter correctly
- /admin/telemetry/rich respects admin auth
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.services.device_models import to_marketing_name


# --- device_models -------------------------------------------------------

def test_to_marketing_name_known_iphone():
    assert to_marketing_name("iPhone17,3") == "iPhone 16"


def test_to_marketing_name_known_ipad():
    assert to_marketing_name("iPad16,4") == 'iPad Pro 11" (M4)'


def test_to_marketing_name_simulator():
    assert to_marketing_name("arm64") == "Simulator (Apple Silicon)"


def test_to_marketing_name_unknown_preserves_raw():
    name = to_marketing_name("iPhone99,99")
    assert "iPhone99,99" in name
    assert name.startswith("Unknown")


def test_to_marketing_name_none():
    assert to_marketing_name(None) is None
    assert to_marketing_name("") is None


# --- /v1/events/ping accepts new fields ----------------------------------

def _device_uuid() -> str:
    return str(uuid.uuid4())


def test_ping_accepts_new_device_fields(client):
    resp = client.post(
        "/v1/events/ping",
        json={
            "event_type": "app_start",
            "device_id": _device_uuid(),
            "app_version": "1.13",
            "os_version": "26.5",
            "device_model": "iPhone17,3",
            "app_locale": "en_US",
        },
    )
    assert resp.status_code == 204


def test_ping_backward_compat_without_new_fields(client):
    """Old iOS builds that don't know about device_model still work."""
    resp = client.post(
        "/v1/events/ping",
        json={
            "event_type": "meeting_start",
            "device_id": _device_uuid(),
            "meeting_id": "m-1",
            "model_id": "cloudzap/auto",
            "app_version": "1.12",
            "os_version": "26.5",
        },
    )
    assert resp.status_code == 204


def test_ping_rejects_overly_long_device_model(client):
    resp = client.post(
        "/v1/events/ping",
        json={
            "event_type": "app_start",
            "device_id": _device_uuid(),
            "device_model": "x" * 100,  # exceeds 64-char cap
        },
    )
    assert resp.status_code == 422  # Pydantic validation


# --- /admin/telemetry/rich -----------------------------------------------

def _seed_event(client, **kwargs):
    body = {
        "event_type": "app_start",
        "device_id": _device_uuid(),
    }
    body.update(kwargs)
    r = client.post("/v1/events/ping", json=body)
    assert r.status_code == 204
    return body["device_id"]


def test_rich_endpoint_returns_expected_shape(client):
    headers = {"X-Admin-Key": "test-admin-key"}
    resp = client.get("/webhooks/admin/telemetry/rich?days=30", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "days", "filters", "kpis", "version_series", "models",
        "devices", "os_versions", "heatmap", "funnel", "options",
    ):
        assert key in data, f"missing key {key}"
    for opt_key in ("app_versions", "os_versions", "device_models", "model_ids"):
        assert opt_key in data["options"]


def test_rich_endpoint_kpi_shape(client):
    _seed_event(client, app_version="1.13", os_version="26.5", device_model="iPhone17,3")
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    k = resp.json()["kpis"]
    for key in (
        "total_events", "distinct_devices", "distinct_users",
        "app_starts", "meeting_starts", "meeting_stops", "avg_duration_sec",
    ):
        assert key in k


def test_rich_endpoint_applies_version_filter(client):
    _seed_event(client, app_version="1.12")
    _seed_event(client, app_version="1.13")
    headers = {"X-Admin-Key": "test-admin-key"}
    all_resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers=headers,
    ).json()
    only_113 = client.get(
        "/webhooks/admin/telemetry/rich?days=30&app_version=1.13",
        headers=headers,
    ).json()
    # Filtered total events <= unfiltered total events.
    assert only_113["kpis"]["total_events"] <= all_resp["kpis"]["total_events"]
    # And the version_series only contains 1.13.
    versions_in_series = {s["name"] for s in only_113["version_series"]}
    assert versions_in_series.issubset({"1.13"})


def test_rich_endpoint_applies_device_filter(client):
    _seed_event(client, device_model="iPhone17,3")
    _seed_event(client, device_model="iPhone18,1")
    only_17 = client.get(
        "/webhooks/admin/telemetry/rich?days=30&device_model=iPhone17,3",
        headers={"X-Admin-Key": "test-admin-key"},
    ).json()
    devices = [d["device_model"] for d in only_17["devices"]]
    assert all(d in ("iPhone17,3", "unknown") for d in devices)


def test_rich_endpoint_null_device_labeled_pre113(client):
    """Events from pre-1.13 builds have no device_model on the wire.
    The dashboard label should clearly identify this bucket so it
    doesn't look like a mountain of unrecognized device codes."""
    _seed_event(
        client,
        event_type="meeting_start",
        # explicitly no device_model
        model_id="cloudzap/auto",
        meeting_id="null-device-test",
    )
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    ).json()
    null_buckets = [
        d for d in resp["devices"]
        if d["device_model"] == "unknown"
    ]
    assert null_buckets, "expected a NULL device_model bucket"
    assert "pre-1.13" in null_buckets[0]["marketing_name"].lower()


def test_rich_endpoint_devices_carry_marketing_name(client):
    _seed_event(
        client,
        event_type="meeting_start",
        device_model="iPhone17,3",
        model_id="cloudzap/auto",
        meeting_id="m-x",
    )
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    ).json()
    devices = [d for d in resp["devices"] if d["device_model"] == "iPhone17,3"]
    assert devices, "expected an iPhone17,3 row"
    assert devices[0]["marketing_name"] == "iPhone 16"


def test_rich_endpoint_options_lists_distinct_values(client):
    _seed_event(client, app_version="1.11", os_version="26.5")
    _seed_event(client, app_version="1.13", os_version="26.5.1")
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    ).json()
    assert "1.11" in resp["options"]["app_versions"]
    assert "1.13" in resp["options"]["app_versions"]
    assert "26.5" in resp["options"]["os_versions"]
    assert "26.5.1" in resp["options"]["os_versions"]


def test_rich_endpoint_funnel_orders_stages(client):
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    ).json()
    stages = [s["stage"] for s in resp["funnel"]]
    assert stages == ["App start", "Meeting start", "Meeting stop"]


def test_rich_endpoint_requires_admin(client):
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "wrong"},
    )
    assert resp.status_code == 403
