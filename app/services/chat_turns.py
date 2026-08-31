"""Chat turn records: a retry costs a lookup, not a second model call.

2026-08-29. Scott asked one question with two documents attached and GP built
the full answer THREE times, at $0.2738, $0.2623 and $0.2633, and delivered
none of them. Each attempt re-uploaded 400,653 bytes over a two-bar uplink and
re-ran an identical 10,798-token prompt, because nothing on either side could
say "you already asked me this and I already answered it".

The client mints a `turn_id` per USER-AUTHORED turn and resends it unchanged on
every retry. GP keys on `(user_id, turn_id)`:

    no record   -> register, run, store the finished body
    in flight   -> do NOT start a second upstream call
    terminal    -> return the stored body, no second bill

Two decisions worth stating, because both look like details and are not:

**The whole response body is stored, not a summary of it.** A replay must be
indistinguishable from the original answer, because the client parses it with
the same branch. Storing text alone would silently drop feature_state,
search_state, cta_only and the token counts, and the drop would only show up
as a subtly different UI on the retry path, which is the path nobody looks at.

**Registration happens BEFORE the first SSE frame, always.** This is a contract
with SS, not an implementation detail. Their retry logic reads the first frame
as "GP accepted this turn": no frames means resend in full because nothing was
billed, one frame means look it up rather than pay twice. That inference is
only sound if a frame can never precede a registration. Today the ordering
holds by accident of where the frame sits; this module exists partly so it
holds by construction, and so that moving the heartbeat later (to cover the
5.32s of pre-flight) cannot silently invert their logic.

The in-flight registry is in-memory, exactly as `generation_turns` does it: a
GP restart kills the upstream call with the process, so those ids honestly
resolve to 404 rather than to a promise nothing is keeping.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.chat_turns")

EXPIRY_HOURS = 6      # one clock with generated_files and generations
POLL_AFTER_SECONDS = 3

# An in-flight entry older than this is treated as gone, by `running_info`
# and by `begin` alike.
#
# It is a leak valve, not a timeout. The handler has many early-return paths
# (gates, entitlement refusals, validation) and a registration that is not
# matched by a `finish` or an `abandon` on EVERY one of them would strand the
# id: a retry would then read `in_progress` forever, and the client would
# poll a turn that no longer exists. Requiring every future early return to
# remember a cleanup is a rule that depends on everyone remembering, which is
# the kind that fails. This heals instead.
#
# 240s sits above GP's own 180s stream cap, so a turn we would still serve is
# never mistaken for a leak.
_IN_FLIGHT_MAX_SECONDS = 240

# (user_id, turn_id) -> {"started_at": datetime}
_IN_FLIGHT: dict[tuple[str, str], dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def running_info(user_id: str, turn_id: str) -> dict | None:
    """Elapsed fields for an in-flight turn, or None.

    No `expected_seconds`, deliberately, unlike the generation lane. We hold a
    measured expectation for an artifact build and we hold none for a chat
    turn, and a number we cannot stand behind is the fabrication this whole
    lane began as. Elapsed is observed; a countdown would not be.
    """
    entry = _IN_FLIGHT.get((user_id, turn_id))
    if entry is None:
        return None
    if (_now() - entry["started_at"]).total_seconds() > _IN_FLIGHT_MAX_SECONDS:
        _IN_FLIGHT.pop((user_id, turn_id), None)
        return None
    return {
        "status": "in_progress",
        "started_at": entry["started_at"].isoformat(),
        "elapsed_seconds": int((_now() - entry["started_at"]).total_seconds()),
        "poll_after_seconds": POLL_AFTER_SECONDS,
    }


def begin(user_id: str, turn_id: str) -> bool:
    """Register an in-flight turn. False when that id is already running,
    which is the caller's signal to answer `turn_in_progress` instead of
    starting a second upstream call."""
    key = (user_id, turn_id)
    entry = _IN_FLIGHT.get(key)
    if entry is not None:
        if (_now() - entry["started_at"]).total_seconds() <= _IN_FLIGHT_MAX_SECONDS:
            return False
        logger.info("chat_turns: reclaiming stale in-flight entry turn_id=%s", turn_id)
    _IN_FLIGHT[key] = {"started_at": _now()}
    return True


def abandon(user_id: str, turn_id: str) -> None:
    """Drop the in-flight entry without recording a terminal row, for a turn
    that died before anything billable ran (a gate raised, say). The id then
    reads as unknown, which is correct: a full resend is the cheap answer when
    no upstream call happened."""
    _IN_FLIGHT.pop((user_id, turn_id), None)


async def finish(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    app_id: str | None,
    turn_id: str,
    status: str,            # "done" | "failed"
    body: dict | None = None,
    error: dict | None = None,
) -> None:
    """Record the terminal state and clear the in-flight entry."""
    entry = _IN_FLIGHT.pop((user_id, turn_id), None)
    started = entry["started_at"] if entry else _now()
    completed = _now()
    await db.execute(
        """INSERT OR REPLACE INTO chat_turns
           (turn_id, user_id, app_id, status, body_json, error_json,
            started_at, completed_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            turn_id, user_id, app_id, status,
            json.dumps(body) if body is not None else None,
            json.dumps(error) if error else None,
            started.isoformat(), completed.isoformat(),
            (completed + timedelta(hours=EXPIRY_HOURS)).isoformat(),
        ),
    )
    await db.commit()


async def lookup_terminal(
    db: aiosqlite.Connection, user_id: str, turn_id: str
) -> dict | None:
    """The stored turn for its OWNER, or None.

    Expired rows are excluded rather than reported, so never-existed,
    expired, not-yours and lost-to-restart are one indistinguishable answer,
    matching /v1/generations. The client's response to all four is the same:
    resend the turn in full.
    """
    row = await (await db.execute(
        "SELECT * FROM chat_turns WHERE turn_id = ? AND user_id = ? "
        "AND expires_at > ?",
        (turn_id, user_id, _now().isoformat()),
    )).fetchone()
    if row is None:
        return None
    if row["status"] == "done":
        return {"status": "done", "replayed": True,
                "body": json.loads(row["body_json"] or "{}")}
    return {"status": "failed", "replayed": True,
            "error": json.loads(row["error_json"] or "{}")}


async def purge_expired(db: aiosqlite.Connection) -> int:
    """Delete expired turn rows, in the same sweep as the other two stores."""
    cur = await db.execute(
        "DELETE FROM chat_turns WHERE expires_at <= ?", (_now().isoformat(),)
    )
    await db.commit()
    n = cur.rowcount or 0
    if n:
        logger.info("chat_turns: purged %d expired turn record(s)", n)
    return n
