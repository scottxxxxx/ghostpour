"""Background sweep that resets allocations for INACTIVE users.

`lazy_reset_if_due` (app/services/allocation_reset.py) zeroes a user's
`monthly_used_usd` (and overage/searches) once their `allocation_resets_at`
is in the past — but it only runs on the usage path, i.e. when the user
makes a request. A user who never makes a request after their reset date
keeps a stale counter forever.

That stale counter is read directly by the Overview "Allocation Alerts"
panel (`users.monthly_used_usd / monthly_cost_limit_usd`), so an inactive
user stuck at/over their limit produces a permanent false allocation alert
even though their actual usage (`usage_log`) is empty. Observed in prod:
lk2race@gmail.com pinned at $0.35/$0.35 = 100% with zero usage_log rows.

This daemon periodically applies the SAME lazy_reset_if_due to every
active user whose reset date has passed, so inactive users get reset at
the period boundary like everyone else. It reuses lazy_reset_if_due
verbatim (atomic WHERE-guarded UPDATE), so it's race-safe against the
usage-path reset.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

from app.services.allocation_reset import lazy_reset_if_due

logger = logging.getLogger(__name__)


async def sweep_due_allocations(
    db: aiosqlite.Connection,
    now: datetime | None = None,
) -> int:
    """Reset every active user whose `allocation_resets_at` is in the past.

    Returns the number of users actually reset. Selects candidates up front
    (a snapshot), then resets each via lazy_reset_if_due — which re-checks
    the due condition under an atomic guard, so a user reset by the usage
    path in between is simply skipped (counts as not-reset here)."""
    if now is None:
        now = datetime.now(timezone.utc)

    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """SELECT id FROM users
            WHERE is_active = 1
              AND allocation_resets_at IS NOT NULL
              AND allocation_resets_at < ?""",
        (now.isoformat(),),
    )
    due = await cursor.fetchall()

    reset_count = 0
    for row in due:
        try:
            if await lazy_reset_if_due(db, row["id"], now=now):
                reset_count += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not abort the sweep
            logger.warning("allocation_reset_sweep: failed for user %s: %s", row["id"], e)

    return reset_count


async def run_daemon(app) -> None:
    """Lifespan-spawned loop. First sweep after a short delay so it doesn't
    tangle with startup, then every
    `allocation_reset_sweep_interval_seconds`. Fail-soft: an exception in
    any sweep must not kill the loop."""
    await asyncio.sleep(15.0)
    while True:
        try:
            settings = app.state.settings
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
            async with aiosqlite.connect(db_path) as db:
                n = await sweep_due_allocations(db)
            if n:
                logger.info("allocation_reset_sweep reset %d due user(s)", n)
            else:
                logger.debug("allocation_reset_sweep: no users due")
        except Exception as e:  # noqa: BLE001
            logger.warning("allocation_reset_sweep tick failed: %s", e)

        try:
            await asyncio.sleep(app.state.settings.allocation_reset_sweep_interval_seconds)
        except asyncio.CancelledError:
            return
