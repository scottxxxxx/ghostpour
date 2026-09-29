"""Shoulder Surf Companion telemetry (2026-09-29, agreed with SS).

The four companion events ride the same anonymous ping but land in their own
tables. The failure these guard is invisible from either end: on
telemetry_events a companion launch would count as a new iPhone install
(telemetry_devices) and an iPhone active device (distinct_devices), and every
companion-only field would have been dropped with a 204.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

DMG = "https://storage.googleapis.com/weirtech-public-share/desktop/ShoulderSurfCompanion.dmg"
WIN = "https://storage.googleapis.com/weirtech-public-share/desktop/windows/ShoulderSurfCompanion-{}.exe"


def _u() -> str:
    return str(uuid.uuid4())


def _rows(db, sql, *args):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _start(**over):
    body = {"event_type": "companion_start", "device_id": _u(), "platform": "mac",
            "arch": "arm64", "first_run": True, "app_version": "1.0", "app_build": "412",
            "os_version": "15.1", "app_locale": "en_US"}
    body.update(over)
    return body


def test_a_companion_launch_is_stored_with_every_field_and_counted_as_an_install(client, tmp_db_path):
    body = _start()
    r = client.post("/v1/events/ping", json=body, headers={"X-App-ID": "shouldersurf"})
    assert r.status_code == 204, r.text
    row = _rows(tmp_db_path, "SELECT event_type, platform, arch, first_run, app_version, app_build, "
                "os_version, app_locale, app_id, user_id FROM companion_events WHERE device_id = ?",
                body["device_id"])
    assert row == [("companion_start", "mac", "arm64", 1, "1.0", "412", "15.1", "en_US", "shouldersurf", None)]
    inst = _rows(tmp_db_path, "SELECT platform, arch, last_version, last_build FROM companion_installs "
                 "WHERE device_id = ?", body["device_id"])
    assert inst == [("mac", "arm64", "1.0", "412")]


def test_a_companion_is_never_an_iphone_install_or_active_device(client, tmp_db_path):
    body = _start()
    assert client.post("/v1/events/ping", json=body).status_code == 204
    assert _rows(tmp_db_path, "SELECT 1 FROM telemetry_events WHERE device_id = ?", body["device_id"]) == []
    assert _rows(tmp_db_path, "SELECT 1 FROM telemetry_devices WHERE device_id = ?", body["device_id"]) == []


def test_the_iphone_ping_still_pins_its_device(client, tmp_db_path):
    """The companion branch must not swallow the lane it sits beside."""
    dev = _u()
    assert client.post("/v1/events/ping", json={"event_type": "app_start", "device_id": dev}).status_code == 204
    assert _rows(tmp_db_path, "SELECT 1 FROM telemetry_devices WHERE device_id = ?", dev) == [(1,)]
    assert _rows(tmp_db_path, "SELECT event_type FROM telemetry_events WHERE device_id = ?", dev) == [("app_start",)]


def test_a_later_launch_updates_the_install_version_not_its_first_seen(client, tmp_db_path):
    body = _start()
    client.post("/v1/events/ping", json=body)
    first = _rows(tmp_db_path, "SELECT first_seen_at FROM companion_installs WHERE device_id = ?", body["device_id"])
    client.post("/v1/events/ping", json=dict(body, first_run=False, app_build="430"))
    after = _rows(tmp_db_path, "SELECT first_seen_at, last_build FROM companion_installs WHERE device_id = ?",
                  body["device_id"])
    assert after == [(first[0][0], "430")]
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM companion_events WHERE device_id = ?", body["device_id"]) == [(2,)]


def test_a_session_stop_keeps_its_meeting_and_duration(client, tmp_db_path):
    body = _start(event_type="companion_session_stop", platform="windows", arch="x64",
                  first_run=None, meeting_id="m-1", duration_seconds=1830)
    assert client.post("/v1/events/ping", json=body).status_code == 204
    assert _rows(tmp_db_path, "SELECT platform, meeting_id, duration_seconds FROM companion_events "
                 "WHERE device_id = ?", body["device_id"]) == [("windows", "m-1", 1830)]


def test_the_phones_link_names_the_user_and_the_companion_and_is_not_an_install(client, tmp_db_path):
    phone, comp = _u(), _u()
    body = {"event_type": "companion_linked", "device_id": phone, "user_id": "user-9",
            "companion_device_id": comp, "companion_platform": "windows", "companion_version": "1.0"}
    assert client.post("/v1/events/ping", json=body).status_code == 204
    assert _rows(tmp_db_path, "SELECT user_id, platform, companion_device_id, companion_platform, "
                 "companion_version FROM companion_events WHERE device_id = ?", phone) == \
        [("user-9", "ios", comp, "windows", "1.0")]
    assert _rows(tmp_db_path, "SELECT 1 FROM companion_installs WHERE device_id IN (?, ?)", phone, comp) == []


def test_a_companion_event_without_a_platform_is_refused_not_stored(client, tmp_db_path):
    body = _start()
    del body["platform"]
    assert client.post("/v1/events/ping", json=body).status_code == 422
    assert client.post("/v1/events/ping", json=_start(platform="linux")).status_code == 422
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM companion_events") == [(0,)]


def test_an_unknown_event_type_is_still_refused_loudly(client):
    assert client.post("/v1/events/ping", json=_start(event_type="companion_bogus")).status_code == 422


def test_an_unknown_field_is_accepted_but_said_out_loud(client, caplog):
    """It used to vanish with a 204 and no trace (the to_name shape)."""
    caplog.set_level(logging.WARNING, logger="app.routers.telemetry")
    r = client.post("/v1/events/ping", json={"event_type": "app_start", "device_id": _u(),
                                             "brand_new_field_xyz": 1})
    assert r.status_code == 204
    assert any("ping_unknown_fields" in m and "brand_new_field_xyz" in m for m in caplog.messages)


def test_download_mac_is_counted_and_redirected(client, tmp_db_path):
    r = client.get("/v1/companion/download/mac", follow_redirects=False,
                   headers={"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605.1.15",
                            "Referer": "https://shouldersurf.com/desktop?x=1"})
    assert r.status_code == 302 and r.headers["location"] == DMG
    assert _rows(tmp_db_path, "SELECT platform, arch, ua_family, referrer_host FROM companion_downloads") == \
        [("mac", None, "safari", "shouldersurf.com")]


def test_download_windows_picks_the_cpu_and_defaults_to_x64(client, tmp_db_path):
    r = client.get("/v1/companion/download/windows?arch=arm64", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == WIN.format("arm64")
    r = client.get("/v1/companion/download/windows", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == WIN.format("x64")
    r = client.get("/v1/companion/download/windows?arch=sparc", follow_redirects=False)
    assert r.headers["location"] == WIN.format("x64")
    assert sorted(_rows(tmp_db_path, "SELECT arch FROM companion_downloads")) == [("arm64",), ("x64",), ("x64",)]


def test_an_unknown_platform_is_a_404_and_not_counted(client, tmp_db_path):
    assert client.get("/v1/companion/download/linux", follow_redirects=False).status_code == 404
    assert _rows(tmp_db_path, "SELECT COUNT(*) FROM companion_downloads") == [(0,)]


def test_the_admin_view_splits_by_platform_and_leaves_iphone_out(client, tmp_db_path):
    mac, win = _u(), _u()
    client.post("/v1/events/ping", json=_start(device_id=mac))
    client.post("/v1/events/ping", json=_start(device_id=win, platform="windows", arch="x64"))
    client.post("/v1/events/ping", json=_start(event_type="companion_session_stop", device_id=win,
                                               platform="windows", first_run=None, duration_seconds=600))
    client.post("/v1/events/ping", json={"event_type": "companion_linked", "device_id": _u(), "user_id": "u-1",
                                         "companion_device_id": mac, "companion_platform": "mac"})
    client.post("/v1/events/ping", json={"event_type": "app_start", "device_id": _u()})  # iPhone, must not appear
    client.get("/v1/companion/download/mac", follow_redirects=False)
    r = client.get("/webhooks/admin/telemetry/companion?days=7", headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200, r.text
    rows = {x["platform"]: x for x in r.json()["by_platform"]}
    assert rows["mac"] == {"platform": "mac", "downloads": 1, "installs": 1, "active_installs": 1, "launches": 1,
                           "linked_accounts": 1, "sessions": 0, "session_minutes": 0}
    assert rows["windows"] == {"platform": "windows", "downloads": 0, "installs": 1, "active_installs": 1,
                               "launches": 1, "linked_accounts": 0, "sessions": 1, "session_minutes": 10}
    assert r.json()["installs_all_time"] == {"mac": 1, "windows": 1}
