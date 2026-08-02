"""Purchase email alerts (Scott 2026-07-27): every paid tier transition
emails through the alerting service; non-paid transitions stay silent;
repeated purchases never dedup into one incident.
"""

import sqlite3

import pytest

from tests.conftest import _insert_user


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _incidents(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT subject, details_json FROM alert_incidents "
            "WHERE category='subscription_purchase'").fetchall()
    finally:
        conn.close()


@pytest.mark.anyio
async def test_paid_events_email_and_never_dedup(client, app_env):
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "buyer-1")
    await notify_purchase("buyer-1", "free", "pro", "upgrade")
    await notify_purchase("buyer-1", "free", "plus", "trial_start")
    await notify_purchase("buyer-1", "plus", "plus", "trial_to_paid")
    # a second upgrade later the same month must be a fresh incident
    await notify_purchase("buyer-1", "plus", "pro", "upgrade")
    rows = _incidents(_db(app_env))
    assert len(rows) == 4
    assert len({r[0] for r in rows}) == 4      # unique fingerprints
    assert "buyer-1" in rows[0][1]


@pytest.mark.anyio
async def test_offer_id_lands_in_alert_details(client, app_env):
    """Offer-code redemptions carry the ASC offer reference name into the
    ops email so the operator can tell which campaign/friend code it was."""
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "buyer-offer")
    await notify_purchase("buyer-offer", "free", "pro", "upgrade",
                          offer_id="friend-launch-1")
    await notify_purchase("buyer-offer", "free", "pro", "upgrade")
    rows = _incidents(_db(app_env))
    assert len(rows) == 2
    with_offer = [r for r in rows if "friend-launch-1" in r[1]]
    assert len(with_offer) == 1


@pytest.mark.anyio
async def test_non_paid_events_stay_silent(client, app_env):
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "leaver-1")
    for ev in ("downgrade", "cancellation", "expire", "refund",
               "account_deleted"):
        await notify_purchase("leaver-1", "pro", "free", ev)
    assert _incidents(_db(app_env)) == []


@pytest.mark.anyio
async def test_fires_through_tier_change_chokepoint_without_cq(client, app_env,
                                                               monkeypatch):
    """The hook lives ahead of notify_tier_change's cq_base_url
    early-return, so the email fires even with CQ unconfigured."""
    from app.config import get_settings
    from app.services.context_quilt import notify_tier_change
    _insert_user(_db(app_env), "buyer-2")
    monkeypatch.setattr(get_settings(), "cq_base_url", "")
    await notify_tier_change("buyer-2", "free", "pro", "upgrade")
    assert len(_incidents(_db(app_env))) == 1


# --- Production gate (2026-08-02) ------------------------------------
#
# TestFlight testers cycle their own sandbox renewals, so "new Pro
# subscriber" mail was arriving for purchases that were never real. Two
# such alerts in one morning is what surfaced it.


def _seed_env_event(db_path: str, user_id: str, environment: str) -> None:
    import uuid
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, to_tier, source, environment,
            effective_at, recorded_at)
           VALUES (?,?,?,?,'assn',?, '2026-08-02T00:00:00+00:00',
                   '2026-08-02T00:00:00+00:00')""",
        (uuid.uuid4().hex, user_id, "subscribed", "pro", environment))
    conn.commit()
    conn.close()


@pytest.mark.anyio
async def test_sandbox_purchase_does_not_email(client, app_env):
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "tf-buyer")
    await notify_purchase("tf-buyer", "free", "pro", "upgrade",
                          environment="Sandbox")
    assert _incidents(_db(app_env)) == []


@pytest.mark.anyio
async def test_production_purchase_still_emails(client, app_env):
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "real-buyer")
    await notify_purchase("real-buyer", "free", "pro", "upgrade",
                          environment="Production")
    rows = _incidents(_db(app_env))
    assert len(rows) == 1
    assert '"environment": "Production"' in rows[0][1]


@pytest.mark.anyio
async def test_environment_inferred_from_account_history(client, app_env):
    """Callers that do not pass environment still get the gate: the
    account's own Sandbox history is the tell."""
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "hist-tf")
    _seed_env_event(_db(app_env), "hist-tf", "Sandbox")
    await notify_purchase("hist-tf", "free", "pro", "upgrade")
    assert _incidents(_db(app_env)) == []

    _insert_user(_db(app_env), "hist-prod")
    _seed_env_event(_db(app_env), "hist-prod", "Production")
    await notify_purchase("hist-prod", "free", "pro", "upgrade")
    assert len(_incidents(_db(app_env))) == 1


@pytest.mark.anyio
async def test_unknown_environment_still_emails_tagged(client, app_env):
    """A missed real sale costs more than a spurious alert, so an
    unclassifiable purchase emails and says so."""
    from app.services.subscription_alerts import notify_purchase
    _insert_user(_db(app_env), "mystery-buyer")
    await notify_purchase("mystery-buyer", "free", "pro", "upgrade")
    rows = _incidents(_db(app_env))
    assert len(rows) == 1
    assert '"environment": "unknown"' in rows[0][1]
