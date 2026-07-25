"""Whale alert: ops email when a user's month-to-date provider cost
crosses the threshold (docs/decisions/cost-and-limits.md, 2026-07-25).

Deliberately NOT a user-facing cap: nothing changes for the user, the
operator just finds out. Reads the never-resetting usage_log ledger
(not the allocation counter, which resets on tier transitions), so a
whale can't vanish by flipping tiers. Dedup rides the alerting
service's open-incident fingerprint: one email per user per month
while the incident stays open; the quiet-window auto-resolve means a
still-climbing whale re-fires occasionally, which is intended.
"""

import logging

import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)


async def check_whale(db: aiosqlite.Connection, user_id: str) -> None:
    """Best-effort: never raises into the request path."""
    try:
        settings = get_settings()
        threshold = float(settings.cost_alert_threshold_usd or 0)
        if threshold <= 0:
            return
        cursor = await db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0), "
            "strftime('%Y-%m', 'now') FROM usage_log "
            "WHERE user_id = ? AND request_timestamp >= "
            "date('now', 'start of month')", (user_id,))
        mtd_cost, month = await cursor.fetchone()
        if mtd_cost < threshold:
            return

        cursor = await db.execute(
            "SELECT email, tier FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        email = row["email"] if row else "(deleted)"
        tier = row["tier"] if row else "?"
        cursor = await db.execute(
            "SELECT COALESCE(call_type, '(none)'), "
            "ROUND(SUM(estimated_cost_usd), 2) c FROM usage_log "
            "WHERE user_id = ? AND request_timestamp >= "
            "date('now', 'start of month') "
            "GROUP BY call_type ORDER BY c DESC LIMIT 3", (user_id,))
        top = {ct: c for ct, c in await cursor.fetchall()}

        from app.services.alerting import report_incident
        await report_incident(
            db,
            category="user_cost_whale",
            subject=f"{user_id[:8]}:{month}",
            details={
                "user_id": user_id,
                "email": email,
                "tier": tier,
                "month_to_date_usd": round(mtd_cost, 2),
                "threshold_usd": threshold,
                "top_call_types": top,
            },
            from_addr=settings.alert_email_from,
        )
    except Exception:
        logger.exception("cost_alerts: whale check failed (non-fatal)")
