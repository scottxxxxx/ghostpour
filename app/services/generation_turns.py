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

EXPIRY_HOURS = 6  # a RUNNING row's horizon; a lost row cannot sit forever
# Discovery (2026-09-05, SS contract): a done turn and its staged files
# live long enough for "next time I open the project"; a failed one long
# enough to be told about. Measured on prod before choosing: one
# generation and 40KB of files in the prior 30 days.
DONE_EXPIRY_HOURS = 7 * 24
FAILED_EXPIRY_HOURS = 24
LOST_TO_RESTART = {"code": "lost_to_restart",
                   "message": "GhostPour restarted while this file was being built"}
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


async def record_start(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    app_id: str | None,
    generation_id: str,
    project_id: str | None = None,
    meeting_id: str | None = None,
    session_id: str | None = None,
    question: str | None = None,
) -> None:
    """Write the RUNNING row at begin() (discovery contract, 2026-09-05).

    Until this existed nothing was persisted before finish(), which is why
    a restart mid-build was a 404. Now the row carries where the turn
    belongs (project, meeting, the client's conversation id) and the
    question, and `sweep_lost_to_restart` turns any row still running at
    boot into an honest failed row.
    """
    started = _now()
    await db.execute(
        """INSERT OR REPLACE INTO generations
           (generation_id, user_id, app_id, status, text, error_json, files_json,
            started_at, completed_at, expires_at,
            project_id, meeting_id, session_id, question, acked_at)
           VALUES (?, ?, ?, 'running', NULL, NULL, '[]', ?, ?, ?, ?, ?, ?, ?, NULL)""",
        (generation_id, user_id, app_id, started.isoformat(), started.isoformat(),
         (started + timedelta(hours=EXPIRY_HOURS)).isoformat(),
         project_id, meeting_id, session_id, question),
    )
    await db.commit()


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
    hours = DONE_EXPIRY_HOURS if status == "done" else FAILED_EXPIRY_HOURS
    expires = (completed + timedelta(hours=hours)).isoformat()
    files = generated_files or []
    # UPDATE keeps the context columns record_start wrote; INSERT covers a
    # turn that never recorded a start (older callers, tests).
    cur = await db.execute(
        """UPDATE generations SET status = ?, text = ?, error_json = ?, files_json = ?,
                  completed_at = ?, expires_at = ?
           WHERE generation_id = ? AND user_id = ?""",
        (status, text, json.dumps(error) if error else None, json.dumps(files),
         completed.isoformat(), expires, generation_id, user_id),
    )
    if not cur.rowcount:
        await db.execute(
            """INSERT INTO generations
               (generation_id, user_id, app_id, status, text, error_json,
                files_json, started_at, completed_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (generation_id, user_id, app_id, status, text,
             json.dumps(error) if error else None, json.dumps(files),
             started.isoformat(), completed.isoformat(), expires),
        )
    await db.commit()
    if status == "done" and files:
        # The staged bytes live as long as the row that points at them. A
        # failure here is logged, never raised: the turn is already
        # recorded and the client already has the response.
        from app.services.generated_files import extend_expiry
        try:
            await extend_expiry(db, [f.get("file_id") for f in files if f.get("file_id")], expires)
        except Exception as e:  # noqa: BLE001
            logger.warning("generations: could not extend staged file expiry for %s: %s", generation_id, e)


async def lookup_terminal(
    db: aiosqlite.Connection, user_id: str, generation_id: str
) -> dict | None:
    """Stored terminal turn for the OWNER, or None (expired rows excluded
    — the endpoint's uniform-404 contract)."""
    row = await (await db.execute(
        "SELECT * FROM generations WHERE generation_id = ? AND user_id = ? "
        "AND expires_at > ? AND status != 'running'",
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


def _entry(row) -> dict:
    """The discovery entry: the single GET's shape plus where the turn
    belongs. Row keys are read defensively so a database that predates
    the discovery columns still renders."""
    keys = row.keys()
    get = lambda k: row[k] if k in keys else None  # noqa: E731
    out: dict = {"generation_id": row["generation_id"], "status": row["status"],
                 "app_id": row["app_id"], "project_id": get("project_id"),
                 "meeting_id": get("meeting_id"), "session_id": get("session_id"),
                 "question": get("question") or "", "started_at": row["started_at"],
                 "completed_at": row["completed_at"] if row["status"] != "running" else None,
                 "expires_at": row["expires_at"], "acked_at": get("acked_at")}
    if row["status"] == "done":
        out["text"] = row["text"] or ""
        out["generated_files"] = json.loads(row["files_json"] or "[]")
    elif row["status"] == "failed":
        out["error"] = json.loads(row["error_json"] or "{}")
    return out


async def list_for_scope(
    db: aiosqlite.Connection, user_id: str, *,
    project_id: str | None = None, meeting_id: str | None = None,
) -> list[dict]:
    """Every non-expired turn of the OWNER in this scope, newest first.

    Running rows are included only while this process is actually running
    them (with the honest-progress fields); a running row this process
    does not know is lost to a restart and waits for the sweep. Empty
    list for a scope with nothing, never a 404.
    """
    where = ["user_id = ?", "expires_at > ?"]
    args: list = [user_id, _now().isoformat()]
    if project_id:
        where.append("project_id = ?"); args.append(project_id)
    if meeting_id:
        where.append("meeting_id = ?"); args.append(meeting_id)
    rows = await (await db.execute(
        f"SELECT * FROM generations WHERE {' AND '.join(where)} ORDER BY started_at DESC", args,
    )).fetchall()
    out = []
    for row in rows:
        if row["status"] == "running":
            info = running_info(user_id, row["generation_id"])
            if info is None:
                continue
            out.append({**_entry(row), **info})
        else:
            out.append(_entry(row))
    return out


async def ack(db: aiosqlite.Connection, user_id: str, generation_id: str) -> dict | None:
    """Mark a terminal row as presented. Idempotent: the first ack sets
    acked_at, every later one returns the same value. None when there is
    no such live terminal row for this owner."""
    await db.execute(
        "UPDATE generations SET acked_at = ? WHERE generation_id = ? AND user_id = ? "
        "AND acked_at IS NULL AND status != 'running' AND expires_at > ?",
        (_now().isoformat(), generation_id, user_id, _now().isoformat()),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM generations WHERE generation_id = ? AND user_id = ? "
        "AND status != 'running' AND expires_at > ?",
        (generation_id, user_id, _now().isoformat()),
    )).fetchone()
    return _entry(row) if row else None


async def sweep_lost_to_restart(db: aiosqlite.Connection) -> int:
    """At boot, every row still 'running' was killed with the old process.
    Turn it into an honest failed row (24h) instead of a silent 404."""
    now = _now()
    cur = await db.execute(
        """UPDATE generations SET status = 'failed', error_json = ?, completed_at = ?,
                  expires_at = ? WHERE status = 'running'""",
        (json.dumps(LOST_TO_RESTART), now.isoformat(),
         (now + timedelta(hours=FAILED_EXPIRY_HOURS)).isoformat()),
    )
    await db.commit()
    n = cur.rowcount or 0
    if n:
        logger.warning("generations: %d turn(s) lost to restart, marked failed", n)
    return n


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
