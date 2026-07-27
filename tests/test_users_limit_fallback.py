"""Dashboard users list: the allocation gauge falls back to the TIER's
monthly limit when the per-user override column is NULL (2026-07-27).

users.monthly_cost_limit_usd is an override and is NULL for nearly every
row; rendering NULL as unlimited showed every free user at "0.0 / ∞ hrs"
while the budget gate (which reads the tier config) was enforcing the
free cap the whole time. The gauge must reflect what enforcement does.
"""

import sqlite3

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _null_limit(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE users SET monthly_cost_limit_usd = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def test_null_override_falls_back_to_tier_limit(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_free_null", tier="free")
    _null_limit(tmp_db_path, "u_free_null")

    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    row = next(u for u in users if u["id"] == "u_free_null")
    # Free tier is capped in config; the gauge must show that cap, not ∞.
    assert row["monthly_limit_usd"] > 0
    assert row["hours_limit"] != -1


def test_explicit_override_still_wins(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_override", tier="free",
                 monthly_limit=9.99)
    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    row = next(u for u in users if u["id"] == "u_override")
    assert row["monthly_limit_usd"] == 9.99


def test_unlimited_tier_still_shows_infinity(client, tmp_db_path):
    """Pro's tier limit is -1 (unlimited); NULL override on pro must still
    render as unlimited, not inherit some bogus number."""
    _insert_user(tmp_db_path, user_id="u_pro_null", tier="pro")
    _null_limit(tmp_db_path, "u_pro_null")
    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    row = next(u for u in users if u["id"] == "u_pro_null")
    assert row["hours_limit"] == -1
