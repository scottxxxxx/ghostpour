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


# --- rolling 7-day active users (2026-08-03) -------------------------


def test_rolling_7d_active_users_is_distinct_not_a_sum(client, app_env):
    """Scott's ask: for each date, how many users were active in the
    trailing 7 days.

    It cannot be derived on the client by summing the daily series, because
    the rollup stores COUNTS rather than identities, and adding seven days
    of distinct-user counts double-counts anyone who appeared on more than
    one of them, which is most active users. This test is the proof: one
    user pinging on three separate days must read as 1, not 3.
    """
    import sqlite3
    import uuid
    from datetime import datetime, timedelta, timezone

    db_path = app_env["CZ_DATABASE_URL"].split("///")[-1]
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc)
    # Same user on three days; a second user on one of them.
    for offset in (0, 1, 2):
        conn.execute(
            "INSERT INTO telemetry_events (id, event_type, device_id, user_id, "
            "received_at) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, "app_start", "dev-a", "user-a",
             (now - timedelta(days=offset)).isoformat()))
    conn.execute(
        "INSERT INTO telemetry_events (id, event_type, device_id, user_id, "
        "received_at) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, "app_start", "dev-b", "user-b", now.isoformat()))
    conn.commit()
    conn.close()

    r = client.get("/webhooks/admin/telemetry/summary?days=30",
                   headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200
    series = r.json()["series"]["active_users_7d"]
    assert series, "no rolling series returned"
    today = max(p["day"] for p in series)
    latest = next(p["value"] for p in series if p["day"] == today)
    assert latest == 2, (
        f"expected 2 distinct users in the trailing 7 days, got {latest}; "
        "a sum of daily counts would have said 4")


# --- regular users: active in the window AND known before it (2026-10-15) ---


def test_regular_users_7d_counts_only_actives_whose_account_predates_the_window(
        client, app_env):
    """Scott's ask: users active in the last 7 days who had also used the app
    before those 7 days. Three users, one answer each way:
    old account + active now        -> regular
    new account (2 days) + active   -> active, NOT regular
    old account, last seen 10d ago  -> neither
    old TEST account + active now   -> neither (exclude_from_stats, 10-15)
    So today's point is active 2, regular 1."""
    import sqlite3
    import uuid
    from datetime import datetime, timedelta, timezone

    db_path = app_env["CZ_DATABASE_URL"].split("///")[-1]
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc)
    for uid, age_days, excluded in (("u-old", 40, 0), ("u-new", 2, 0),
                                    ("u-gone", 40, 0), ("u-test", 40, 1)):
        created = (now - timedelta(days=age_days)).isoformat()
        conn.execute(
            "INSERT INTO users (id, apple_sub, tier, created_at, updated_at, "
            "exclude_from_stats) VALUES (?,?,?,?,?,?)",
            (uid, f"sub-{uid}", "free", created, created, excluded))
    for uid, ago in (("u-old", 0), ("u-new", 0), ("u-gone", 10), ("u-test", 0)):
        conn.execute(
            "INSERT INTO telemetry_events (id, event_type, device_id, user_id, "
            "received_at) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, "app_start", f"dev-{uid}", uid,
             (now - timedelta(days=ago)).isoformat()))
    conn.commit()
    conn.close()

    r = client.get("/webhooks/admin/telemetry/summary?days=30",
                   headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200
    series = r.json()["series"]
    today = now.date().isoformat()
    active = {p["day"]: p["value"] for p in series["active_users_7d"]}
    regular = {p["day"]: p["value"] for p in series["regular_users_7d"]}
    assert active[today] == 2, (
        f"expected u-old and u-new active, got {active[today]}; "
        "3 means the excluded test account was counted")
    assert regular[today] == 1, (
        f"expected only u-old as regular today, got {regular[today]}; "
        "2 means the excluded test account was counted")
