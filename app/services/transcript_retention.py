"""Retention sweep for `meeting_transcripts`.

The 30-day window itself is Scott's 2026-07-21 call and is unchanged
here. What changed (Scott, 2026-08-16) is WHEN it is enforced. The
DELETE used to live only inside `init_db()`, so a row past its window
survived until the next container start; real retention was "30 days
plus however long until the next deploy", and rows measurably sat past
the line. This module owns the window and the statement, `init_db`
calls it at boot, and `main.py` runs it on a loop, so the promise holds
without a deploy.

The durable copies live where they should: the phone keeps the
transcript the user sees and CQ keeps the distilled memory. A report
requested for a meeting whose transcript we have already dropped comes
back 404 `no_meeting_data`, which is the client's cue to re-send the
capture and retry (`app/routers/reports.py`). That path is deliberate,
not a gap.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Aligned with the meeting_reports window on purpose: regeneration
# insurance is worth nothing if the transcript outlives the report or
# the other way round.
TRANSCRIPT_RETENTION_DAYS = 30

# How often the sweep runs once the app is up. A 30-day window does not
# need a tight loop; hourly matches the existing generated_files sweep
# so there is one cadence to reason about, and it bounds the overshoot
# to an hour instead of a deploy cycle.
SWEEP_INTERVAL_SECONDS = 3600


async def purge_expired_transcripts(
    db: aiosqlite.Connection,
    *,
    retention_days: int = TRANSCRIPT_RETENTION_DAYS,
) -> int:
    """Delete transcripts past the retention window. Returns the count.

    Commits its own work: the sweep loop owns a short-lived connection
    of its own, and at boot `init_db` commits again harmlessly.
    """
    cursor = await db.execute(
        "DELETE FROM meeting_transcripts"
        f" WHERE created_at < datetime('now', '-{int(retention_days)} days')"
    )
    deleted = cursor.rowcount or 0
    await db.commit()
    if deleted:
        logger.info(
            "meeting_transcripts: purged %d row(s) older than %d days",
            deleted, retention_days,
        )
    return deleted
