"""Retention sweeps for every table that has a TTL.

The windows themselves are unchanged and each remains Scott's call. What
changed (2026-08-16) is WHEN they are enforced. Every one of these
DELETEs used to live only inside `init_db()`, so a row past its window
survived until the next container start: real retention was "N days plus
however long until the next deploy", and rows were measurably sitting
past the line. This module owns the windows and the statements, `init_db`
calls it at boot, and `main.py` runs it on a loop, so the promise holds
without a deploy.

Declarative on purpose. A new TTL is a row in the table below rather
than another DELETE that only ever runs at startup, which is exactly how
the five here drifted into the same defect one at a time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)

# Aligned with the meeting_reports window on purpose: regeneration
# insurance is worth nothing if the transcript outlives the report or
# the other way round.
TRANSCRIPT_RETENTION_DAYS = 30

# How often the sweep runs once the app is up. These windows are days
# long and do not need a tight loop; hourly matches the existing
# generated_files sweep so there is one cadence to reason about, and it
# bounds the overshoot to an hour instead of a deploy cycle.
SWEEP_INTERVAL_SECONDS = 3600

# Raw telemetry is purged, but a device's first-seen date has to outlive
# the events that established it. `init_db` runs this absorb before its
# purge; the scheduled sweep has to do the same or a device that first
# appeared and then aged out between two deploys loses its true
# first_seen_at forever. INSERT OR IGNORE keeps the earliest value, so
# re-running is a no-op for known devices.
_ABSORB_TELEMETRY_DEVICES = """
    INSERT OR IGNORE INTO telemetry_devices (device_id, first_seen_at)
    SELECT device_id, MIN(received_at) FROM telemetry_events
    GROUP BY device_id
"""


@dataclass(frozen=True)
class Sweep:
    table: str
    column: str          # the timestamp column the window is measured on
    days: int
    why: str             # kept next to the number so the two cannot drift
    before: str = ""     # statement that must run first, if any


SWEEPS: tuple[Sweep, ...] = (
    Sweep("meeting_transcripts", "created_at", TRANSCRIPT_RETENTION_DAYS,
          "The phone keeps the transcript the user sees and CQ keeps the "
          "distilled memory. GP's copy exists for report generation, "
          "regeneration insurance, and cleanup debugging."),
    Sweep("meeting_reports", "created_at", 30,
          "Cached reports, aligned with the transcript window above."),
    Sweep("plan_snapshots", "created_at", 365,
          "Dated plan versions slip tracking diffs against. A year of "
          "baseline is generous for slip, and the bound keeps GP from "
          "quietly becoming a forever archive of structured project state."),
    Sweep("email_events", "received_at", 90,
          "Webhook audit log, kept long enough for spam-complaint and "
          "bounce attribution. The suppression list is NOT swept here: a "
          "suppressed address stays suppressed forever unless lifted."),
    Sweep("telemetry_events", "received_at", 30,
          "Raw events. Aggregates live in telemetry_daily_rollups and are "
          "kept indefinitely.",
          before=_ABSORB_TELEMETRY_DEVICES),
)


async def purge_expired(db: aiosqlite.Connection) -> dict[str, int]:
    """Run every sweep. Returns rows deleted per table.

    One table failing must not stop the others: a missing table on an
    older schema, or a lock, should cost that sweep and nothing else.
    Commits once at the end; at boot `init_db` commits again harmlessly.
    """
    deleted: dict[str, int] = {}
    for s in SWEEPS:
        try:
            if s.before:
                await db.execute(s.before)
            cursor = await db.execute(
                f"DELETE FROM {s.table}"
                f" WHERE {s.column} < datetime('now', '-{int(s.days)} days')"
            )
            deleted[s.table] = cursor.rowcount or 0
        except Exception as e:  # noqa: BLE001 — one table must not sink the rest
            logger.warning("retention sweep failed for %s: %s", s.table, e)
            deleted[s.table] = 0
    await db.commit()
    dropped = {t: n for t, n in deleted.items() if n}
    if dropped:
        logger.info("retention: purged %s", dropped)
    return deleted


async def purge_expired_transcripts(
    db: aiosqlite.Connection,
    *,
    retention_days: int = TRANSCRIPT_RETENTION_DAYS,
) -> int:
    """Just the transcript window. Kept as its own entry point because
    the transcript TTL is the one with a user-visible consequence: a
    report requested for a purged meeting comes back 404
    `no_meeting_data`, which is the client's cue to re-send the capture
    and retry (`app/routers/reports.py`). That path is deliberate.
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
