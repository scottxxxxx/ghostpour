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


@pytest.mark.anyio
async def test_late_claim_resolves_open_incident(client, app_env):
    """SS's replay queue can deliver verify-receipt days after the alert
    legitimately fired. The claim must close the open incident so the
    late arrival reads as healing, not a second failure. A later
    re-orphan of the same txn opens (and emails) a fresh incident."""
    import aiosqlite
    from app.routers.apple_webhooks import _alert_if_still_unmatched
    from app.services.alerting import resolve_incident

    await _alert_if_still_unmatched("8880001", {"p": 1}, grace_seconds=0)
    assert _incidents(_db(app_env)) == [("8880001",)]

    db = await aiosqlite.connect(_db(app_env))
    try:
        assert await resolve_incident(db, "assn_unmatched", "8880001") is True
        # already closed: second resolve is a no-op
        assert await resolve_incident(db, "assn_unmatched", "8880001") is False
    finally:
        await db.close()

    conn = sqlite3.connect(_db(app_env))
    open_rows = conn.execute(
        "SELECT COUNT(*) FROM alert_incidents "
        "WHERE category='assn_unmatched' AND resolved_at IS NULL").fetchone()
    conn.close()
    assert open_rows[0] == 0

    # a fresh orphan for the same txn after resolution opens a NEW incident
    await _alert_if_still_unmatched("8880001", {"p": 2}, grace_seconds=0)
    assert len(_incidents(_db(app_env))) == 2
