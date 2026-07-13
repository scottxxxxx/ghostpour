"""Generation turn records + in-flight registry (phase 2 rescue,
handoff: ss-documents-phase2-generation-wire.md Part 4).

The client mints a `generation_id` and sends it on every confirmed
generation turn. GP records the finished turn — text answer, staged-file
entries, terminal status — against that id on the same 6h clock as the
staging bytes, so a client that died mid-turn can reconstruct the whole
turn from GET /v1/generations/{id}. A resend carrying an already-terminal
id returns the stored result (no second sandbox bill); a still-running id
409s with honest-progress fields so a relaunched client resumes the true
elapsed time, never an elapsed-from-zero timer.

The running state is in-memory by design: a GP restart kills the in-flight
provider call with the process, so post-restart those ids honestly resolve
404 → the client's regenerate card.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.generation_turns")

EXPIRY_HOURS = 6  # same clock as generated_files staging
POLL_AFTER_SECONDS = 5
DEFAULT_EXPECTED_SECONDS = 150

# (user_id, generation_id) -> {"started_at": datetime, "expected_seconds": int}
_IN_FLIGHT: dict[tuple[str, str], dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def running_info(user_id: str, generation_id: str) -> dict | None:
    """Honest-progress fields for an in-flight turn, or None."""
    entry = _IN_FLIGHT.get((user_id, generation_id))
    if entry is None:
        return None
    elapsed = int((_now() - entry["started_at"]).total_seconds())
    return {
        "status": "running",
        "started_at": entry["started_at"].isoformat(),
        "elapsed_seconds": elapsed,
        "expected_seconds": entry["expected_seconds"],
        "poll_after_seconds": POLL_AFTER_SECONDS,
    }


def begin(user_id: str, generation_id: str,
          expected_seconds: int = DEFAULT_EXPECTED_SECONDS) -> bool:
    """Register an in-flight turn. False if that id is already running
    (caller answers 409 with running_info)."""
    key = (user_id, generation_id)
    if key in _IN_FLIGHT:
        return False
    _IN_FLIGHT[key] = {"started_at": _now(), "expected_seconds": expected_seconds}
    return True


def abandon(user_id: str, generation_id: str) -> None:
    """Drop the in-flight entry without recording a terminal row — used
    when the turn dies before anything meaningful ran (e.g. a pre-provider
    gate raised)."""
    _IN_FLIGHT.pop((user_id, generation_id), None)


async def finish(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    app_id: str | None,
    generation_id: str,
    status: str,  # "done" | "failed"
    text: str | None = None,
    error: dict | None = None,
    generated_files: list[dict] | None = None,
) -> None:
    """Record the terminal state and clear the in-flight entry."""
    entry = _IN_FLIGHT.pop((user_id, generation_id), None)
    started = entry["started_at"] if entry else _now()
    completed = _now()
    await db.execute(
        """INSERT OR REPLACE INTO generations
           (generation_id, user_id, app_id, status, text, error_json,
            files_json, started_at, completed_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            generation_id, user_id, app_id, status, text,
            json.dumps(error) if error else None,
            json.dumps(generated_files or []),
            started.isoformat(), completed.isoformat(),
            (completed + timedelta(hours=EXPIRY_HOURS)).isoformat(),
        ),
    )
    await db.commit()


async def lookup_terminal(
    db: aiosqlite.Connection, user_id: str, generation_id: str
) -> dict | None:
    """Stored terminal turn for the OWNER, or None (expired rows excluded
    — the endpoint's uniform-404 contract)."""
    row = await (await db.execute(
        "SELECT * FROM generations WHERE generation_id = ? AND user_id = ? "
        "AND expires_at > ?",
        (generation_id, user_id, _now().isoformat()),
    )).fetchone()
    if row is None:
        return None
    out: dict = {"status": row["status"]}
    if row["status"] == "done":
        out["text"] = row["text"] or ""
        out["generated_files"] = json.loads(row["files_json"] or "[]")
    else:
        out["error"] = json.loads(row["error_json"] or "{}")
    return out


async def purge_expired(db: aiosqlite.Connection) -> int:
    """Delete expired generation rows. Runs in the same sweep as the
    generated_files purge (one clock, one sweep)."""
    cur = await db.execute(
        "DELETE FROM generations WHERE expires_at <= ?", (_now().isoformat(),)
    )
    await db.commit()
    n = cur.rowcount or 0
    if n:
        logger.info("generations: purged %d expired turn record(s)", n)
    return n
