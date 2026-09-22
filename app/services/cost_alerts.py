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

import asyncio
import logging

import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)

# Both statements are planned by tests/test_whale_check_query_plan.py against
# the migrated schema, so an edit that stops them using the covering index
# fails there and not on a cold disk in production.
#
# Prod, 2026-09-22 00:29Z (the N-400 auditor's cold turn, read from the
# journal and the usage row): the model finished at 51.78 s and the envelope
# left at 57.98 s. The six seconds in between were THIS check, run inline in
# log_usage before the streamed envelope: with only (user_id,
# request_timestamp) usable, SQLite fetched each of the QA account's 3,731
# rows (239 MB of metadata this month) from the table for the cost column,
# and, past the threshold, again for call_type. Measured on prod: 581 ms and
# 7,411 ms cool, 11 ms and 236 ms warm. The same disease #1018 cured for the
# budget gate, one query over. Two fixes, either sufficient, both taken: the
# statements are covered by idx_usage_user_date_cost_calltype, and the check
# runs AFTER the request on its own connection (check_whale_later).
MONTH_COST_SQL = (
    "SELECT COALESCE(SUM(estimated_cost_usd), 0), "
    "strftime('%Y-%m', 'now') FROM usage_log "
    "WHERE user_id = ? AND request_timestamp >= "
    "date('now', 'start of month')")
TOP_CALL_TYPES_SQL = (
    "SELECT COALESCE(call_type, '(none)'), "
    "ROUND(SUM(estimated_cost_usd), 2) c FROM usage_log "
    "WHERE user_id = ? AND request_timestamp >= "
    "date('now', 'start of month') "
    "GROUP BY call_type ORDER BY c DESC LIMIT 3")

_LIVE: set[asyncio.Task] = set()


def check_whale_later(user_id: str) -> asyncio.Task:
    """Run the check after the request, on a connection of its own, because
    the request's connection (the streamed path's in particular) is closed
    the moment the tail returns. The turn never waits for an alert."""
    task = asyncio.ensure_future(_check_whale_own_connection(user_id))
    _LIVE.add(task)
    task.add_done_callback(_LIVE.discard)
    return task


async def _check_whale_own_connection(user_id: str) -> None:
    try:
        from app import database
        conn = aiosqlite.connect(database._db_path)
        # aiosqlite's connection IS a thread, started on the first await.
        # The pinned 0.20 leaves it non-daemon, so a background check whose
        # event loop closed mid-connect left a thread that kept the
        # interpreter alive: CI printed "4445 passed" and then hung for fifty
        # minutes, twice, on 2026-09-22 (locally 0.22 exits, which is the
        # off-the-pin venv lying). Daemon before start, and drained at
        # shutdown, so nothing best-effort can outlive the process.
        conn.daemon = True
        async with conn as db:
            db.row_factory = aiosqlite.Row
            await check_whale(db, user_id)
    except Exception:
        logger.exception("cost_alerts: whale check could not open its connection (non-fatal)")


async def drain(timeout: float = 5.0) -> int:
    """Wait for the checks still in flight, then cancel the rest. Called at
    app shutdown so a restart never races a half-open connection. Returns
    how many were still running when it was called."""
    live = [t for t in _LIVE if not t.done()]
    if not live:
        return 0
    try:
        await asyncio.wait_for(asyncio.gather(*live, return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        for t in live:
            if not t.done():
                t.cancel()
        await asyncio.gather(*live, return_exceptions=True)
    return len(live)


async def check_whale(db: aiosqlite.Connection, user_id: str) -> None:
    """Best-effort: never raises into the request path."""
    try:
        settings = get_settings()
        threshold = float(settings.cost_alert_threshold_usd or 0)
        if threshold <= 0:
            return
        cursor = await db.execute(MONTH_COST_SQL, (user_id,))
        mtd_cost, month = await cursor.fetchone()
        if mtd_cost < threshold:
            return

        cursor = await db.execute(
            "SELECT email, tier FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        email = row["email"] if row else "(deleted)"
        tier = row["tier"] if row else "?"
        cursor = await db.execute(TOP_CALL_TYPES_SQL, (user_id,))
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
