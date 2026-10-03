"""A push to the operator's phones for every new account (Scott, 2026-10-02:
"I want to get an iphone push anytime a new user hits our GP server and a
count of the unique users and daily users").

Fired from the two places an account is born in app/routers/auth.py: a new
Sign in with Apple, and a new anonymous purchase account. An anonymous
account converting to Apple is the same person and does not push.

Rides operator_push, so the recipients are the `operator-alerts` doc's
push_user_ids and the category `new_user` must be listed in its
push_categories (removing it there turns these off without a deploy).

Background task with its own connection, never awaited by the sign-in: a
best-effort side effect must not sit in front of the response (the 6.2 s
whale check, 2026-09-22). Never raises. Capped per hour so a burst of
account creation cannot flood a phone.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiosqlite

logger = logging.getLogger(__name__)

CATEGORY = "new_user"
MAX_PER_HOUR = 20
_CENTRAL = ZoneInfo("America/Chicago")
_APP_NAMES = {"shouldersurf": "Shoulder Surf", "techrehearsal": "Tech Rehearsal"}
_sent_at: deque[float] = deque()
_tasks: set = set()


def _today_start_utc(now: datetime | None = None) -> str:
    """Midnight Central, as a UTC ISO string comparable to GP's timestamps."""
    now = now or datetime.now(timezone.utc)
    local_midnight = now.astimezone(_CENTRAL).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).isoformat()


async def counts(db: aiosqlite.Connection) -> dict:
    """Unique users (accounts), new today and active today, Central day.
    Automation (test harness) accounts are left out of all three."""
    since = _today_start_utc()
    total = (await (await db.execute(
        "SELECT COUNT(*) FROM users WHERE is_active = 1 AND tier != 'automation'")).fetchone())[0]
    new_today = (await (await db.execute(
        "SELECT COUNT(*) FROM users WHERE created_at >= ? AND tier != 'automation'",
        (since,))).fetchone())[0]
    active_today = (await (await db.execute(
        """SELECT COUNT(DISTINCT l.user_id) FROM usage_log l JOIN users u ON u.id = l.user_id
            WHERE l.request_timestamp >= ? AND u.tier != 'automation'""",
        (since,))).fetchone())[0]
    return {"total": total, "new_today": new_today, "active_today": active_today}


def message(kind: str, app_id: str | None, c: dict) -> str:
    who = "Signed in with Apple" if kind == "apple" else "No sign-in (purchase account)"
    app = _APP_NAMES.get(app_id or "", app_id or "an unknown app")
    return (f"{who} on {app}. Users: {c['total']} total, {c['new_today']} new today, "
            f"{c['active_today']} active today.")


def _under_cap() -> bool:
    now = time.monotonic()
    while _sent_at and now - _sent_at[0] > 3600:
        _sent_at.popleft()
    if len(_sent_at) >= MAX_PER_HOUR:
        return False
    _sent_at.append(now)
    return True


async def run(user_id: str, kind: str, app_id: str | None, settings) -> dict:
    """Count, then push. Returns operator_push's outcome, or a skip reason."""
    try:
        if not _under_cap():
            logger.warning("new_user_push: hourly cap %d reached, skipping user=%s",
                           MAX_PER_HOUR, user_id[:8])
            return {"sent": 0, "skipped": "hourly_cap"}
        from app.database import _db_path
        from app.services.operator_push import push_incident
        async with aiosqlite.connect(_db_path) as db:
            db.row_factory = aiosqlite.Row
            c = await counts(db)
            return await push_incident(db, category=CATEGORY, label="New user",
                                       subject=message(kind, app_id, c), settings=settings,
                                       collapse_key=user_id)
    except Exception:  # noqa: BLE001, never the sign-in's problem
        logger.exception("new_user_push failed user=%s", user_id[:8])
        return {"sent": 0, "skipped": "exception"}


def schedule(user_id: str, kind: str, app_id: str | None, settings) -> None:
    """Fire and forget from a request handler."""
    try:
        task = asyncio.get_running_loop().create_task(run(user_id, kind, app_id, settings))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001
        logger.exception("new_user_push: could not schedule user=%s", user_id[:8])
