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

import asyncio

import logging
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)

# Aligned with the meeting_reports window on purpose: regeneration
# insurance is worth nothing if the transcript outlives the report or
# the other way round.
TRANSCRIPT_RETENTION_DAYS = 30

# The exact provider request and reply ride every usage_log row
# (metadata.raw_request / raw_response, usage_tracker.py). The request holds
# the transcript as sent, so without a trim the 30 day transcript rule above
# did not hold: the same text lived forever inside usage_log (Scott,
# 2026-10-02, who ruled the trim). After this window both keys are removed
# from the row; the row itself (tokens, cost, timing) stays, because billing
# and the dashboards count it. Accounts listed below keep theirs
# indefinitely, for debugging: Scott's own (scott@weirtech.com), at his
# request. Opaque ids only, this repo is public.
RAW_REQUEST_RETENTION_DAYS = TRANSCRIPT_RETENTION_DAYS
KEEP_RAW_REQUESTS_FOR: tuple[str, ...] = (
    "fa4d903c-24c0-45d5-9fdb-b5496e32501b",
)

# usage_log rows themselves, for the anonymous form apps only (Scott,
# 2026-10-14, for the N-400 privacy copy: "add the 90 day expiry"). Each row
# carries user_id, case_id and IP-derived city, so keeping them forever kept
# one person's interview linkable forever. After this window the ROW is
# deleted. Billing math survives it: the anonymous allowance sums usage_log
# for its lifetime cap, so an anonymous account's expiring spend is first
# added to anonymous_spend_carry, the same carry a "Delete all my data" uses,
# and the cap cannot be reset by waiting. The per-application allowance
# (case_id) has no carry, so an application older than the window starts
# that one bucket again; the lifetime cap still holds. Monthly budgets only
# read the current month. ShoulderSurf and Tech Rehearsal rows are not in
# scope: their dashboards and per-user history are a separate call.
USAGE_LOG_RETENTION_DAYS = 90
USAGE_LOG_EXPIRING_APPS: tuple[str, ...] = ("n400", "i765")
KEEP_USAGE_ROWS_FOR: tuple[str, ...] = KEEP_RAW_REQUESTS_FOR

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
    Sweep("meeting_heartbeats", "received_at", 30,
          "One row per meeting, the last heartbeat it sent. Same standing "
          "as the raw events it belongs with."),
    Sweep("companion_events", "received_at", 180,
          "Companion launches, links and sessions. Install counts and "
          "versions in use live in companion_installs, which is kept."),
    Sweep("companion_downloads", "received_at", 180,
          "Website download hits for the companions."),
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
            # Commit per table, so no single write transaction spans the
            # whole sweep (2026-10-07: it held the lock past the 5s busy
            # timeout and a chat turn 500'd after its model call).
            await db.commit()
        except Exception as e:  # noqa: BLE001 — one table must not sink the rest
            logger.warning("retention sweep failed for %s: %s", s.table, e)
            deleted[s.table] = 0
    try:
        deleted["usage_log.raw_request"] = await strip_old_raw_requests(db)
    except Exception as e:  # noqa: BLE001, same rule as the sweeps above
        logger.warning("retention: raw_request strip failed: %s", e)
        deleted["usage_log.raw_request"] = 0
    try:
        deleted["usage_log"] = await expire_old_usage_rows(db)
    except Exception as e:  # noqa: BLE001, same rule as the sweeps above
        logger.warning("retention: usage_log expiry failed: %s", e)
        deleted["usage_log"] = 0
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


async def strip_old_raw_requests(
    db: aiosqlite.Connection,
    *,
    retention_days: int = RAW_REQUEST_RETENTION_DAYS,
    keep_for: tuple[str, ...] = KEEP_RAW_REQUESTS_FOR,
    batch_size: int = 50,
) -> int:
    """Remove raw_request and raw_response from usage_log rows past the
    window, except for `keep_for` accounts. Returns rows changed. The
    cutoff is formatted like request_timestamp (ISO with a T), so the
    comparison is exact rather than off by the T against a space."""
    placeholders = ",".join("?" for _ in keep_for) or "''"
    # In batches, each its own short transaction (2026-10-07). As one UPDATE
    # it held the write lock for the whole scan (378 rows took past 03:13:58Z
    # and a chat turn's usage write timed out behind it). The SELECT is a
    # read, which WAL lets run beside writers; only the small UPDATE locks.
    total = 0
    while True:
        cursor = await db.execute(
            f"""SELECT id FROM usage_log
                 WHERE request_timestamp < strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
                   AND (json_extract(metadata, '$.raw_request') IS NOT NULL
                        OR json_extract(metadata, '$.raw_response') IS NOT NULL)
                   AND user_id NOT IN ({placeholders})
                 LIMIT ?""",
            (f"-{int(retention_days)} days", *keep_for, batch_size),
        )
        ids = [r[0] for r in await cursor.fetchall()]
        if not ids:
            return total
        marks = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"""UPDATE usage_log
                   SET metadata = json_remove(metadata, '$.raw_request', '$.raw_response')
                 WHERE id IN ({marks})""", ids)
        await db.commit()
        total += cursor.rowcount or 0
        await asyncio.sleep(0)


async def expire_old_usage_rows(
    db: aiosqlite.Connection,
    *,
    retention_days: int = USAGE_LOG_RETENTION_DAYS,
    apps: tuple[str, ...] = USAGE_LOG_EXPIRING_APPS,
    keep_for: tuple[str, ...] = KEEP_USAGE_ROWS_FOR,
    batch_size: int = 50,
) -> int:
    """Delete usage_log rows of `apps` older than the window, except for
    `keep_for` accounts, carrying an anonymous account's expiring spend into
    anonymous_spend_carry first. Returns rows deleted.

    One (user, app) pair per transaction: the carry and the delete commit
    together, so a crash between them can neither lose spend nor count it
    twice. The cutoff is read once, so the rows summed are the rows deleted."""
    from app.services.anonymous_budget import ANONYMOUS_SUB_PREFIX, CARRY_APPS

    cutoff = (await (await db.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)",
        (f"-{int(retention_days)} days",))).fetchone())[0]
    app_marks = ",".join("?" for _ in apps) or "''"
    keep_marks = ",".join("?" for _ in keep_for) or "''"
    total = 0
    while True:
        pairs = await (await db.execute(
            f"""SELECT DISTINCT user_id, app_id FROM usage_log
                 WHERE request_timestamp < ? AND app_id IN ({app_marks})
                   AND user_id NOT IN ({keep_marks})
                 LIMIT ?""",
            (cutoff, *apps, *keep_for, batch_size))).fetchall()
        if not pairs:
            return total
        for user_id, app_id in pairs:
            spent = float((await (await db.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log"
                " WHERE user_id = ? AND app_id = ? AND request_timestamp < ?",
                (user_id, app_id, cutoff))).fetchone())[0] or 0.0)
            sub = await (await db.execute(
                "SELECT apple_sub FROM users WHERE id = ?", (user_id,))).fetchone()
            if (spent > 0 and app_id in CARRY_APPS and sub and sub[0]
                    and str(sub[0]).startswith(ANONYMOUS_SUB_PREFIX)):
                await db.execute(
                    """INSERT INTO anonymous_spend_carry
                           (sub_hash, app_id, lifetime_usd, day, day_usd, updated_at)
                       VALUES (?, ?, ?, NULL, 0, strftime('%Y-%m-%dT%H:%M:%S', 'now'))
                       ON CONFLICT(sub_hash, app_id) DO UPDATE SET
                           lifetime_usd = lifetime_usd + excluded.lifetime_usd,
                           updated_at = excluded.updated_at""",
                    (sub[0], app_id, spent))
            cursor = await db.execute(
                "DELETE FROM usage_log WHERE user_id = ? AND app_id = ? AND request_timestamp < ?",
                (user_id, app_id, cutoff))
            await db.commit()
            total += cursor.rowcount or 0
            await asyncio.sleep(0)
