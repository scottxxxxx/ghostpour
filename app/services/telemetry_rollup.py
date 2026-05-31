"""Daily rollup of telemetry_events into telemetry_daily_rollups.

Called at startup (idempotent — INSERT OR REPLACE on (day, metric))
covering any days that don't already have a complete rollup. Raw events
purge at 30 days; rollups keep the trend lines intact indefinitely.

Metric keys are flat strings stored on `telemetry_daily_rollups.metric`:

  app_starts                    -- count of event_type='app_start'
  meetings_started              -- count of event_type='meeting_start'
  meetings_stopped              -- count of event_type='meeting_stop'
  distinct_devices              -- count distinct device_id
  distinct_users                -- count distinct user_id (non-null)
  meetings_per_model:<model_id> -- one row per model that had any meeting events
  duration_avg_sec              -- mean duration_seconds across meeting_stop rows
  duration_min_sec              -- min
  duration_max_sec              -- max
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.telemetry_rollup")

# Recompute the trailing N days at every startup. Idempotent (INSERT OR
# REPLACE), so re-running over already-rolled days is cheap and absorbs
# any late-arriving events.
_LOOKBACK_DAYS = 35


async def compute_rollups(db: aiosqlite.Connection) -> int:
    """Compute (and overwrite) daily rollups for the trailing window.

    Returns the number of (day, metric) rows written. Safe to call
    repeatedly; INSERT OR REPLACE on the composite PK ensures convergence.
    """
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(_LOOKBACK_DAYS)]
    total = 0
    for day in days:
        total += await _compute_day(db, day.isoformat())
    await db.commit()
    return total


async def _compute_day(db: aiosqlite.Connection, day: str) -> int:
    """Compute all metrics for one calendar day (UTC). Returns row count."""
    # Date-range filter on received_at — half-open [start, end_exclusive).
    start = f"{day}T00:00:00+00:00"
    end = f"{day}T23:59:59.999999+00:00"
    written = 0

    async def _write(metric: str, value: float) -> None:
        nonlocal written
        await db.execute(
            "INSERT OR REPLACE INTO telemetry_daily_rollups (day, metric, value) VALUES (?, ?, ?)",
            (day, metric, float(value)),
        )
        written += 1

    # Event counts by type
    cursor = await db.execute(
        """SELECT event_type, COUNT(*) AS c
           FROM telemetry_events
           WHERE received_at BETWEEN ? AND ?
           GROUP BY event_type""",
        (start, end),
    )
    counts = {r[0]: r[1] for r in await cursor.fetchall()}
    await _write("app_starts", counts.get("app_start", 0))
    await _write("meetings_started", counts.get("meeting_start", 0))
    await _write("meetings_stopped", counts.get("meeting_stop", 0))

    # Distinct counts
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT device_id) AS d, COUNT(DISTINCT user_id) AS u
           FROM telemetry_events
           WHERE received_at BETWEEN ? AND ?""",
        (start, end),
    )
    row = await cursor.fetchone()
    await _write("distinct_devices", row[0] if row else 0)
    await _write("distinct_users", row[1] if row else 0)

    # Per-model meeting counts (start + stop combined; gives an "active
    # model" signal regardless of whether the stop event landed).
    cursor = await db.execute(
        """SELECT model_id, COUNT(*) AS c
           FROM telemetry_events
           WHERE received_at BETWEEN ? AND ?
             AND event_type IN ('meeting_start','meeting_stop')
             AND model_id IS NOT NULL
           GROUP BY model_id""",
        (start, end),
    )
    for r in await cursor.fetchall():
        await _write(f"meetings_per_model:{r[0]}", r[1])

    # Duration stats (meeting_stop rows that carried a duration)
    cursor = await db.execute(
        """SELECT AVG(duration_seconds), MIN(duration_seconds), MAX(duration_seconds)
           FROM telemetry_events
           WHERE received_at BETWEEN ? AND ?
             AND event_type = 'meeting_stop'
             AND duration_seconds IS NOT NULL""",
        (start, end),
    )
    row = await cursor.fetchone()
    if row and row[0] is not None:
        await _write("duration_avg_sec", row[0])
        await _write("duration_min_sec", row[1])
        await _write("duration_max_sec", row[2])

    return written
