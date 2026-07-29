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


def _mark_trial(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET is_trial = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def test_offer_trial_carries_offer_id_and_intro_trial_does_not(client, tmp_db_path):
    """Offer-code periods and real intro trials both set is_trial; the
    users list splits them via trial_offer_id (from the subscription
    event log) so the dashboard badges OFFER vs TRIAL (Scott 2026-07-28)."""
    import asyncio

    import aiosqlite

    from app.services import subscriptions as subs

    _insert_user(tmp_db_path, user_id="u_offer", tier="pro")
    _insert_user(tmp_db_path, user_id="u_intro", tier="plus")
    _mark_trial(tmp_db_path, "u_offer")
    _mark_trial(tmp_db_path, "u_intro")

    async def seed():
        db = await aiosqlite.connect(tmp_db_path)
        try:
            await subs.record_subscription_event(
                db, user_id="u_offer", event_type="subscribed",
                from_tier="free", to_tier="pro",
                offer_id="friend-test", source="verify_receipt")
            await subs.record_subscription_event(
                db, user_id="u_intro", event_type="subscribed",
                from_tier="free", to_tier="plus",
                source="verify_receipt")
        finally:
            await db.close()
    asyncio.run(seed())

    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    by_id = {u["id"]: u for u in users}
    assert by_id["u_offer"]["trial_offer_id"] == "friend-test"
    assert by_id["u_intro"]["is_trial"] and by_id["u_intro"]["trial_offer_id"] is None


def test_users_list_includes_anonymous_devices_and_app_build(client, tmp_db_path):
    """One user list (Scott 2026-07-28): the users payload carries
    app version/build + telemetry event count per user, and telemetry
    devices that never pinged with a user_id ride along as
    anonymous_devices so the Telemetry-tab directory could be deleted."""
    _insert_user(tmp_db_path, user_id="u_known", tier="free")
    conn = sqlite3.connect(tmp_db_path)
    for dev, uid in (("dev-known", "u_known"), ("dev-anon", None)):
        conn.execute(
            """INSERT INTO telemetry_events
               (id, event_type, device_id, user_id, app_version, app_build,
                os_version, device_model, app_locale, received_at)
               VALUES (?, 'app_start', ?, ?, '1.14', '803', '26.5',
                       'iPhone17,2', 'en_US', '2026-07-28T00:00:00+00:00')""",
            (dev + "-evt", dev, uid))
    conn.commit()
    conn.close()

    resp = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()
    row = next(u for u in resp["users"] if u["id"] == "u_known")
    assert row["app_version"] == "1.14"
    assert row["app_build"] == "803"
    assert row["telemetry_events"] == 1
    anons = resp["anonymous_devices"]
    assert len(anons) == 1
    assert anons[0]["device_id"] == "dev-anon"
    assert anons[0]["app_build"] == "803"
    assert anons[0]["telemetry_events"] == 1


def test_channel_derives_from_telemetry_and_subscription_env(client, tmp_db_path):
    """TestFlight vs App Store split (Scott 2026-07-29, the Raven case:
    a sandbox Pro renewing nightly looks identical to real revenue).
    Telemetry distribution wins; StoreKit environment is the fallback."""
    _insert_user(tmp_db_path, user_id="u_tf", tier="pro")
    _insert_user(tmp_db_path, user_id="u_as", tier="plus")
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        """INSERT INTO telemetry_events
           (id, event_type, device_id, user_id, distribution, received_at)
           VALUES ('e-tf', 'app_start', 'd-tf', 'u_tf', 'sandbox',
                   '2026-07-29T00:00:00+00:00')""")
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, to_tier, source, environment,
            effective_at, recorded_at)
           VALUES ('s-as', 'u_as', 'subscribed', 'plus', 'assn',
                   'Production', '2026-07-29T00:00:00+00:00',
                   '2026-07-29T00:00:00+00:00')""")
    conn.commit()
    conn.close()

    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    by_id = {u["id"]: u for u in users}
    assert by_id["u_tf"]["channel"] == "testflight"
    assert by_id["u_as"]["channel"] == "appstore"
