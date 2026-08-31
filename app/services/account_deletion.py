"""Account deletion (App Review 5.1.1(v)), scoped to the deleting app.

Accounts are shared across apps and not by choice: `users` is keyed on
Apple's subject identifier, which Apple issues per developer TEAM rather
than per app, so the same Apple ID signing into Shoulder Surf and Tech
Rehearsal resolves to one row. Deleting unscoped therefore took the other
app's data with it. Every table is classified into exactly one of three
buckets, and a schema-pinning test fails CI until a newly added
user-keyed table is classified here.

`APP_SCOPED_TABLES` carry an `app_id` and are deleted for the deleting
app only. `APP_OWNED_TABLES` have no `app_id` because the domain belongs
to exactly one app (meetings and projects are Shoulder Surf concepts);
they go when that app goes. `ACCOUNT_TABLES` are properties of the person
rather than of an app - the subscription history, the once-ever welcome
letter, the account row itself - and survive until the LAST app is
deleted, at which point the account is really gone.

Deliberately NOT touched:
- App Store subscription state at Apple: an active subscription outlives
  the account (Apple bills it; only the user can cancel). Deleting the
  users row frees `original_transaction_id`, so a later fresh sign-in
  with a live subscription re-provisions via the normal verify-receipt
  path.
- telemetry_daily_rollups, telemetry_devices: aggregate/anonymous, carry
  no user key.
"""

import logging
import pathlib

import aiosqlite

logger = logging.getLogger(__name__)

# Deleted WHERE user_id = ? AND app_id = ? on a scoped delete.
APP_SCOPED_TABLES = [
    "ad_attribution",
    # Chat turn records (2026-08-30). Stored so a dropped connection costs a
    # lookup instead of a second model call, which means each row holds the
    # user's own question and our answer to it verbatim. Their content, so it
    # goes with them, alongside generations below. The 6h expiry is a cost
    # ceiling, never a deletion promise: an account deleted at minute one
    # must not leave six hours of its own conversation behind.
    "chat_turns",
    # Diagnostic only (poisoned-config-cache detection), but it is keyed to
    # a person and carries their build, so it goes with the rest.
    "config_stalls",
    "generated_files",
    "generations",
    # Meeting shares (2026-08-21): the user's own meeting content, hosted
    # at a public URL. Goes with the account, bytes included; a share must
    # not outlive the person who made it.
    "meeting_shares",
    "plan_snapshots",
    "promo_events",
    # Recall telemetry (2026-08-27): operational, but each row names the
    # person whose turn it measured, so it is theirs and goes with them.
    # The rate it exists to answer is computed per scope, never per user,
    # and survives fine on the remaining rows.
    "recall_observations",
    "refresh_tokens",
    "search_usage",
    "telemetry_events",
    "usage_log",
]

# Tables whose domain belongs to exactly one app, keyed by that app's
# X-App-ID. No app_id column because there is nothing to disambiguate:
# only this app ever writes them.
APP_OWNED_TABLES = {
    "shouldersurf": [
        "meeting_reports",
        "meeting_transcripts",
        "project_prefs",
    ],
}

# Properties of the person, not of an app. Only purged when the last app
# is deleted and the account itself goes away.
ACCOUNT_TABLES = [
    # A deleted account must never receive a queued welcome letter.
    "welcome_email_queue",
    "subscription_events",
    # Config test-audience membership. Kept for audit while the account
    # lives (retiring sets active=0 rather than deleting), but a deleted
    # account cannot be a tester, and holding their id afterwards would
    # contradict what deletion promises. Audit value does not outrank that.
    "config_testers",
]


async def remaining_apps(
    db: aiosqlite.Connection, user_id: str, excluding: str | None = None
) -> list[str]:
    """Apps this account is still a member of, ignoring `excluding`."""
    cursor = await db.execute(
        "SELECT app_id FROM user_apps WHERE user_id = ?", (user_id,))
    apps = [r["app_id"] for r in await cursor.fetchall()]
    return [a for a in apps if a != excluding]


async def _unlink_staged_files(
    db: aiosqlite.Connection, user_id: str, app_id: str | None,
    table: str = "generated_files",
) -> int:
    """Remove staged artifact bytes that sit on disk beside their rows."""
    sql = f"SELECT storage_path FROM {table} WHERE user_id = ?"
    params: list = [user_id]
    if app_id is not None:
        sql += " AND app_id = ?"
        params.append(app_id)

    removed = 0
    cursor = await db.execute(sql, params)
    for row in await cursor.fetchall():
        try:
            pathlib.Path(row["storage_path"]).unlink(missing_ok=True)
            removed += 1
        except OSError:
            logger.exception("account_delete: could not remove staged file")
    return removed


async def delete_user_data(
    db: aiosqlite.Connection, user_id: str, app_id: str | None = None
) -> dict:
    """Purge `user_id`'s data for `app_id`, or everything when app_id is None.

    Returns per-table deleted counts plus `account_removed`: True when
    this was the account's last app and the users row itself is gone,
    False when the account survives for another app. The caller keys Sign
    in with Apple revocation off that flag - revoking Apple's token while
    another app still depends on the account would break its sign-in.

    A None `app_id` (client sent no usable X-App-ID) means a full purge:
    there is no safe way to scope, and under-deleting on a deletion
    request is the worse failure.
    """
    counts: dict[str, int] = {}

    counts["generated_files_disk"] = await _unlink_staged_files(
        db, user_id, app_id)
    counts["meeting_shares_disk"] = await _unlink_staged_files(
        db, user_id, app_id, table="meeting_shares")

    for table in APP_SCOPED_TABLES:
        if app_id is None:
            cur = await db.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        # NOTE: sessions predating the app_id column are unattributed, and
        # they are deliberately LEFT ALONE by a scoped delete. Sweeping
        # them bought no security: /auth/refresh inner-joins users, so a
        # token whose account is gone cannot be exchanged, and the
        # last-app delete removes the account row. All it did was sign the
        # person out of the app they did not delete, which today is nearly
        # every live session (145 of 149 carry no app tag).
        else:
            cur = await db.execute(
                f"DELETE FROM {table} WHERE user_id = ? AND app_id = ?",
                (user_id, app_id))
        counts[table] = cur.rowcount

    owned = (APP_OWNED_TABLES.get(app_id, []) if app_id is not None
             else [t for ts in APP_OWNED_TABLES.values() for t in ts])
    for table in owned:
        cur = await db.execute(
            f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        counts[table] = cur.rowcount

    if app_id is not None:
        cur = await db.execute(
            "DELETE FROM user_apps WHERE user_id = ? AND app_id = ?",
            (user_id, app_id))
        counts["user_apps"] = cur.rowcount

    survivors = await remaining_apps(db, user_id)
    account_removed = app_id is None or not survivors

    if account_removed:
        for table in ACCOUNT_TABLES:
            cur = await db.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            counts[table] = cur.rowcount
        cur = await db.execute("DELETE FROM user_apps WHERE user_id = ?",
                               (user_id,))
        counts["user_apps"] = counts.get("user_apps", 0) + cur.rowcount
        cur = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        counts["users"] = cur.rowcount

    await db.commit()
    counts["account_removed"] = account_removed

    logger.info(
        "account_delete: purged user %s for app=%s (account_removed=%s, "
        "remaining=%s): %s",
        user_id[:8], app_id, account_removed, survivors, counts)
    return counts
