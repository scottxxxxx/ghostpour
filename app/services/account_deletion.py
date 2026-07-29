"""Account deletion (App Review 5.1.1(v)): purge every row keyed to a
user, plus their staged artifact bytes on disk.

`USER_KEYED_TABLES` is the authoritative purge list. A schema-pinning
test asserts it covers every table carrying a `user_id` column, so a
future migration that adds a user-keyed table fails CI until the table
is added here (or deliberately exempted there).

Deliberately NOT touched:
- App Store subscription state at Apple: an active subscription
  outlives the account (Apple bills it; only the user can cancel).
  Deleting the users row frees `original_transaction_id`, so a later
  fresh sign-in with a live subscription re-provisions via the normal
  verify-receipt path.
- telemetry_daily_rollups: aggregate-only, carries no user key.
"""

import logging
import pathlib

import aiosqlite

logger = logging.getLogger(__name__)

USER_KEYED_TABLES = [
    "ad_attribution",
    "generated_files",
    "generations",
    "meeting_reports",
    "meeting_transcripts",
    "plan_snapshots",
    "project_prefs",
    "promo_events",
    "refresh_tokens",
    "search_usage",
    "subscription_events",
    "telemetry_events",
    # A deleted account must never receive a queued welcome letter.
    "welcome_email_queue",
    "usage_log",
]


async def delete_user_data(db: aiosqlite.Connection, user_id: str) -> dict:
    """Purge all data for `user_id`. Returns per-table deleted counts."""
    counts: dict[str, int] = {}

    # Staged artifact bytes live on disk beside their rows.
    files_removed = 0
    cursor = await db.execute(
        "SELECT storage_path FROM generated_files WHERE user_id = ?",
        (user_id,))
    for row in await cursor.fetchall():
        try:
            pathlib.Path(row["storage_path"]).unlink(missing_ok=True)
            files_removed += 1
        except OSError:
            logger.exception("account_delete: could not remove staged file")
    counts["generated_files_disk"] = files_removed

    for table in USER_KEYED_TABLES:
        cur = await db.execute(
            f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        counts[table] = cur.rowcount
    cur = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    counts["users"] = cur.rowcount
    await db.commit()

    logger.info("account_delete: purged user %s: %s", user_id[:8], counts)
    return counts
