"""Telemetry build number (2026-07-22 blind-spot fix): builds 749 and
777 were both marketing "1.14" and indistinguishable on the wire. Pings
now carry optional app_build (CFBundleVersion); the rich dashboard
endpoint reports a per-(version, build) breakdown and the device
directory's latest build."""

from __future__ import annotations

import sqlite3
import uuid


def _ping(client, **over):
    body = {"event_type": "app_start", "device_id": str(uuid.uuid4()),
            "app_version": "1.14"}
    body.update(over)
    r = client.post("/v1/events/ping", json=body,
                    headers={"X-App-ID": "shouldersurf"})
    return r, body["device_id"]


def test_ping_persists_app_build_and_rich_reports_it(client, tmp_db_path):
    r, dev = _ping(client, app_build="777")
    assert r.status_code == 204
    con = sqlite3.connect(tmp_db_path)
    assert con.execute(
        "SELECT app_build FROM telemetry_events WHERE device_id = ?",
        (dev,)).fetchone()[0] == "777"
    con.close()

    _ping(client)  # an old-client ping without the field
    rich = client.get("/webhooks/admin/telemetry/rich?days=7",
                      headers={"X-Admin-Key": "test-admin-key"}).json()
    builds = {(b["version"], b["build"]): b["devices"]
              for b in rich["builds"]}
    assert builds[("1.14", "777")] == 1
    assert builds[("1.14", "")] == 1          # buildless pings still counted
    by_dev = {u["app_build"] for u in rich["directory"]}
    assert "777" in by_dev


def test_ping_without_app_build_still_valid(client, tmp_db_path):
    r, dev = _ping(client)
    assert r.status_code == 204
    con = sqlite3.connect(tmp_db_path)
    assert con.execute(
        "SELECT app_build FROM telemetry_events WHERE device_id = ?",
        (dev,)).fetchone()[0] is None
    con.close()
