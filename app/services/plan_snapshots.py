"""Plan snapshots: every rendered gantt persists its extracted task list.

Groundwork for detailed-gantt v2 slip tracking: slip is computable only
against dated history ("payments was due Jul 10 in the Jun 22 plan, Jul 24
now"), so history collection starts with v1, silently and on BOTH styles.
By the time the Slip sheet ships, existing projects already have weeks of
snapshots and slip populates on day one instead of showing everyone an
empty sheet. Rows are small (task-list JSON) and never purged: slip
analysis wants the full trail.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite


async def history(db: aiosqlite.Connection, *, user_id: str,
                  project_id: str | None) -> list[dict]:
    """Prior plan versions for slip computation, oldest first.

    Ordered by MEETING date (the "as of" the plan itself claims), not
    build time: a user regenerating an old plan later must not corrupt
    the slip timeline. ONE version per as-of date, latest build wins:
    regenerating three times after the same standup must replace that
    day's version, not log movement — extraction jitter on dates nobody
    stated precisely read as "4 moves" on a task that never moved (live
    2026-07-21). No project, no history (slip needs a stable identity)."""
    if not project_id:
        return []
    cur = await db.execute(
        "SELECT tasks_json, meeting_date, created_at FROM plan_snapshots"
        " WHERE user_id = ? AND project_id = ?"
        " ORDER BY COALESCE(meeting_date, substr(created_at, 1, 10)),"
        " created_at LIMIT 100",
        (user_id, project_id),
    )
    by_as_of: dict[str, dict] = {}
    for tasks_json, meeting_date, created_at in await cur.fetchall():
        try:
            tasks = json.loads(tasks_json)
        except ValueError:
            continue
        as_of = meeting_date or created_at[:10]
        # rows arrive (as_of, created_at)-ordered; re-inserting a key
        # keeps its position and swaps the value, so the LAST build of a
        # day wins while the day keeps its place in the timeline
        by_as_of[as_of] = {"as_of": as_of, "created_at": created_at,
                           "tasks": tasks}
    return list(by_as_of.values())


async def record(db: aiosqlite.Connection, *, user_id: str, app_id: str,
                 project_id: str | None, template_id: str,
                 plan: dict) -> None:
    await db.execute(
        """INSERT INTO plan_snapshots
           (id, user_id, app_id, project_id, template_id, project_name,
            meeting_date, tasks_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            app_id,
            project_id,
            template_id,
            plan.get("project"),
            plan.get("meeting_date"),
            json.dumps(plan.get("tasks") or []),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    await db.commit()
