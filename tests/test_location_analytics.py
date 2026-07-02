"""User location in analytics: coarse country/region (GeoIP-derived at
telemetry ingestion) surfaced on the user-detail view, the users list, and a
Telemetry-tab geo breakdown. Data already lands on telemetry_events; these
tests assert the three read surfaces expose it.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _insert_telemetry(db_path, *, user_id, device_id, country, region,
                      event_type="app_start", app_id="shouldersurf", when=None):
    now = when or datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(id, event_type, device_id, user_id, app_id, country, region, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), event_type, device_id, user_id, app_id, country, region, now),
    )
    conn.commit()
    conn.close()


def test_user_detail_exposes_latest_location(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_loc", tier="pro")
    # two pings; the most recent (later timestamp) should win
    _insert_telemetry(tmp_db_path, user_id="u_loc", device_id="d1",
                      country="United States", region="Oregon",
                      when="2026-06-01T00:00:00+00:00")
    _insert_telemetry(tmp_db_path, user_id="u_loc", device_id="d1",
                      country="United States", region="California",
                      when="2026-07-01T00:00:00+00:00")

    d = client.get("/webhooks/admin/user/u_loc?days=90", headers=ADMIN).json()
    assert d["user"]["location"] == {"country": "United States", "region": "California"}


def test_user_detail_null_location_when_no_geo(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_nogeo", tier="free")
    d = client.get("/webhooks/admin/user/u_nogeo?days=90", headers=ADMIN).json()
    assert d["user"]["location"] is None


def test_users_list_includes_location(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_list", tier="pro")
    _insert_telemetry(tmp_db_path, user_id="u_list", device_id="d2",
                      country="Canada", region="Ontario")
    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    row = next(u for u in users if u["id"] == "u_list")
    assert row["location"] == {"country": "Canada", "region": "Ontario"}


def test_telemetry_rich_geo_breakdown(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_a", tier="pro")
    _insert_user(tmp_db_path, user_id="u_b", tier="pro")
    # two devices/users in US-California, one in Canada; one geo-less event ignored
    _insert_telemetry(tmp_db_path, user_id="u_a", device_id="dA",
                      country="United States", region="California")
    _insert_telemetry(tmp_db_path, user_id="u_b", device_id="dB",
                      country="United States", region="California")
    _insert_telemetry(tmp_db_path, user_id="u_a", device_id="dC",
                      country="Canada", region="Ontario")
    _insert_telemetry(tmp_db_path, user_id="u_b", device_id="dD",
                      country=None, region=None)  # no geo -> excluded

    d = client.get("/webhooks/admin/telemetry/rich?days=30", headers=ADMIN).json()
    by_loc = {(r["country"], r["region"]): r for r in d["by_location"]}
    assert (None, None) not in by_loc                       # geo-less row excluded
    ca = by_loc[("United States", "California")]
    assert ca["devices"] == 2 and ca["users"] == 2
    on = by_loc[("Canada", "Ontario")]
    assert on["devices"] == 1 and on["users"] == 1
    # ordered by devices desc: California (2) before Ontario (1)
    order = [(r["country"], r["region"]) for r in d["by_location"]]
    assert order.index(("United States", "California")) < order.index(("Canada", "Ontario"))
