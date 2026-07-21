"""Per-(user, project) preferences, resolved server-side.

Distinct from app/routers/preferences.py (per-user marketing opt-in):
these are keyed by project and read at decision points inside GP (GP is
the brains; the client never has to carry the preference). First key:
`gantt_style` ("simple" | "detailed"), set by the user's reply word at a
gantt offer and reused for every later gantt in that project. The user
changes it by just saying so at any offer; there is no settings UI to
build or sync.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


async def get_pref(db: aiosqlite.Connection, user_id: str, project_id: str,
                   key: str) -> str | None:
    cur = await db.execute(
        "SELECT value FROM project_prefs WHERE user_id = ? AND project_id = ?"
        " AND key = ?",
        (user_id, project_id, key),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def set_pref(db: aiosqlite.Connection, user_id: str, project_id: str,
                   key: str, value: str) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO project_prefs"
        " (user_id, project_id, key, value, updated_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, project_id, key, value,
         datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()
