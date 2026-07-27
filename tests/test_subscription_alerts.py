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
