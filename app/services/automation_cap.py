"""An automation (test) account that passes 100 model calls in an hour is
switched off until Scott clears it (Scott, 2026-09-29, after the spend-cap
outage: "put some reasonable block in place if you get over 100 queries in
an hour that does not allow more until you clear it with me").

It is a latch, not a throttle: the account is deactivated (`is_active = 0`,
the same switch the auth dependency checks on every request) and stays off
until someone turns it back on by hand. Clearing it:

    UPDATE users SET is_active = 1 WHERE id = '<account id>';

The count is the account's usage_log rows in the last rolling hour, one per
model call, indexed on (user_id, request_timestamp). Only the automation tier
is capped; real users never reach this.

Note (said to Scott when it was built): his phone's N-400 app signs in as
the harness account, and a brisk live interview can approach 100 turns in an
hour, so his own testing can trip it too.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import HTTPException

logger = logging.getLogger(__name__)

HOURLY_CAP = 100
CAPPED_TIERS = frozenset({"automation"})


async def enforce(db: aiosqlite.Connection, user) -> None:
    """Raise 403 and latch the account off once it has made HOURLY_CAP calls
    in the last hour. A no-op for every other tier."""
    if getattr(user, "tier", None) not in CAPPED_TIERS:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = await (await db.execute(
        "SELECT COUNT(*) FROM usage_log WHERE user_id = ? AND request_timestamp >= ?",
        (user.id, cutoff))).fetchone()
    used = row[0] if row else 0
    if used < HOURLY_CAP:
        return
    await db.execute(
        "UPDATE users SET is_active = 0, updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user.id))
    await db.commit()
    logger.warning("automation_hourly_cap latched user=%s calls_last_hour=%d cap=%d",
                   user.id, used, HOURLY_CAP)
    try:
        from app.config import get_settings
        from app.services.alerting import report_incident
        await report_incident(
            db, category="automation_hourly_cap", subject=str(user.id)[:8],
            details={"user_id": user.id, "calls_last_hour": used, "cap": HOURLY_CAP,
                     "to_clear": f"UPDATE users SET is_active = 1 WHERE id = '{user.id}'"},
            from_addr=get_settings().alert_email_from)
    except Exception:  # noqa: BLE001  (the block holds whether or not the alert sends)
        logger.exception("automation_hourly_cap: alert failed")
    raise HTTPException(status_code=403, detail={
        "code": "automation_capped",
        "message": "This test account passed its hourly limit and is paused until the operator clears it.",
    })
