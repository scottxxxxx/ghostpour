"""APNs device tokens: one row per phone, owned by whoever registered it.

SS contract 2026-09-05. The primary key is the DEVICE TOKEN alone, not
(user_id, device_token): a token that moves to another user (a shared
phone, a re-signin) must REPLACE its row, and a composite key would keep
both, so the same phone would take two pushes and one of them would be
addressed to the wrong account's file.

`bundle_id` rides on the row because it is the APNs topic, and N-400 will
want this table with a different one; `environment` decides which Apple
host the send goes to and is corrected in place when Apple says the
token belongs to the other one.

The per-user cap is a ceiling, not the collector: Apple's 410
Unregistered is what actually retires a dead token (see apns.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.device_tokens")

MAX_TOKENS_PER_USER = 10
ENVIRONMENTS = ("sandbox", "production")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def other_environment(environment: str | None) -> str:
    return "production" if (environment or "").lower() != "production" else "sandbox"


async def register(
    db: aiosqlite.Connection, *, user_id: str, device_token: str,
    environment: str, bundle_id: str, app_build: str | None = None,
) -> None:
    """Idempotent upsert. Re-registering the same token under a new user
    rewrites the owner in place, which is what the single-column key is
    for. Then prune this user past the cap, oldest last_seen_at first."""
    now = _now()
    await db.execute(
        """INSERT INTO device_tokens
           (device_token, user_id, environment, bundle_id, app_build, created_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_token) DO UPDATE SET
             user_id = excluded.user_id, environment = excluded.environment,
             bundle_id = excluded.bundle_id, app_build = excluded.app_build,
             last_seen_at = excluded.last_seen_at""",
        (device_token, user_id, (environment or "production").lower(),
         bundle_id, app_build, now, now),
    )
    await db.execute(
        """DELETE FROM device_tokens WHERE user_id = ? AND device_token NOT IN (
             SELECT device_token FROM device_tokens WHERE user_id = ?
             ORDER BY last_seen_at DESC LIMIT ?)""",
        (user_id, user_id, MAX_TOKENS_PER_USER),
    )
    await db.commit()


async def unregister(db: aiosqlite.Connection, *, user_id: str, device_token: str) -> None:
    """Sign-out. Owner-scoped so one account cannot delete another's row."""
    await db.execute(
        "DELETE FROM device_tokens WHERE device_token = ? AND user_id = ?",
        (device_token, user_id),
    )
    await db.commit()


async def forget(db: aiosqlite.Connection, device_token: str) -> None:
    """Apple said 410 Unregistered: the app is gone from that phone."""
    await db.execute("DELETE FROM device_tokens WHERE device_token = ?", (device_token,))
    await db.commit()
    logger.info("device_tokens: forgot a token Apple reported unregistered")


async def set_environment(db: aiosqlite.Connection, device_token: str, environment: str) -> None:
    """Apple said BadDeviceToken on one host and accepted it on the other."""
    await db.execute(
        "UPDATE device_tokens SET environment = ? WHERE device_token = ?",
        (environment, device_token),
    )
    await db.commit()


async def for_user(db: aiosqlite.Connection, user_id: str) -> list[dict]:
    rows = await (await db.execute(
        "SELECT * FROM device_tokens WHERE user_id = ? ORDER BY last_seen_at DESC",
        (user_id,),
    )).fetchall()
    return [dict(r) for r in rows]
