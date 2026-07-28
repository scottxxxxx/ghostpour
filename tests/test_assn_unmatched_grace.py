"""assn_unmatched grace window (2026-07-27): Apple's notification beats
the client's verify-receipt by seconds on healthy redemptions
(live-measured 17s), so the alert only fires when the transaction is
STILL unmapped after the grace. Zero-grace in tests to skip the sleep.
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
            "SELECT subject FROM alert_incidents "
            "WHERE category='assn_unmatched'").fetchall()
    finally:
        conn.close()


@pytest.mark.anyio
async def test_still_unmatched_after_grace_alerts(client, app_env):
    from app.routers.apple_webhooks import _alert_if_still_unmatched
    await _alert_if_still_unmatched(
        "9990001", {"product_id": "p"}, grace_seconds=0)
    assert _incidents(_db(app_env)) == [("9990001",)]


@pytest.mark.anyio
async def test_claimed_within_grace_stays_silent(client, app_env):
    """verify-receipt mapped the transaction before the re-check: the
    healthy-race case must produce no alert."""
    from app.routers.apple_webhooks import _alert_if_still_unmatched
    _insert_user(_db(app_env), "claimer-1")
    conn = sqlite3.connect(_db(app_env))
    conn.execute(
        "UPDATE users SET original_transaction_id='9990002' WHERE id='claimer-1'")
    conn.commit()
    conn.close()

    await _alert_if_still_unmatched(
        "9990002", {"product_id": "p"}, grace_seconds=0)
    assert _incidents(_db(app_env)) == []
