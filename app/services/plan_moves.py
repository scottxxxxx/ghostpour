"""A plan MOVES when the Apple transaction behind it is verified on another account.

Before 2026-09-26 verify-receipt only detached the transaction id from any
other account holding it and left that account PAID: every reinstall onto a
fresh identity orphaned a stale paid row. Anonymous purchase accounts make
that a real hole, because a restore on a second device mints a second
anonymous account, and both would then hold the plan.

So when the requester presents the transaction and Apple SIGNED it
(`identity.verified`), the plan moves: the other holder is downgraded, and
the requester inherits its usage for the period instead of the fresh
allowance a plan change otherwise grants, so buying once and restoring onto
new installs cannot mint a new month each time.

ONLY when verified. Receipt enforcement is off in prod, so an unverified
claim is still accepted; letting one downgrade another account would let
anyone who learns a transaction id strip a stranger's plan. An unverified
claim keeps the old behaviour: the id detaches, nothing is downgraded.
Xcode's local StoreKit file signs with a one-certificate chain and is never
verified, so this can only be exercised on sandbox or TestFlight.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger("ghostpour.plan_moves")


async def holders(db: aiosqlite.Connection, otid: str | None, user_id: str) -> list[dict]:
    """Every OTHER account holding this transaction, read before it detaches."""
    if not otid:
        return []
    cur = await db.execute(
        """SELECT id, tier, monthly_used_usd, searches_used, generations_used,
                  allocation_resets_at
           FROM users WHERE original_transaction_id = ? AND id != ?""",
        (otid, user_id))
    return [dict(r) for r in await cur.fetchall()]


async def move_plan(db: aiosqlite.Connection, held_by: list[dict], to_user_id: str,
                    verified: bool, tier_config, otid: str | None = None) -> bool:
    """Downgrade the paid holders and carry their period's usage to the
    requester. True when a plan moved. Call AFTER the requester's plan is
    written, since that write resets usage.

    The purchase's RECORDS move too, or the dashboard mis-counts. Found on
    the first real sandbox restore (2026-09-26): `subscription_status` (the
    per-subscriber row, keyed by transaction) still named the account the
    plan had left, and `subscription_events` held TWO "subscribed" for one
    purchase, one per account, so every restore onto a new install counted
    as a new subscriber. Now the status row follows the plan, the holders'
    events for this transaction move with it, and the "subscribed" this
    request just recorded becomes `reconciled`/`moved` when the purchase
    was already recorded as subscribed."""
    paid = [h for h in held_by if (h.get("tier") or "free") != "free"]
    if not verified or not paid:
        return False
    from app.routers.apple_webhooks import _downgrade_to_free
    src = max(paid, key=lambda h: float(h.get("monthly_used_usd") or 0))
    await db.execute(
        """UPDATE users SET monthly_used_usd = ?, searches_used = ?, generations_used = ?,
                  allocation_resets_at = COALESCE(?, allocation_resets_at)
           WHERE id = ?""",
        (float(src.get("monthly_used_usd") or 0), int(src.get("searches_used") or 0),
         int(src.get("generations_used") or 0), src.get("allocation_resets_at"), to_user_id))
    from app.services.account_groups import link
    for h in paid:
        await _downgrade_to_free(db, h["id"], tier_config)
        # Same person, another device: the dashboard groups them.
        await link(db, h["id"], to_user_id, "restore", otid)
    if otid:
        ids = [h["id"] for h in held_by]
        ph = ",".join("?" * len(ids))
        already_subscribed = await (await db.execute(
            f"SELECT 1 FROM subscription_events WHERE original_transaction_id = ? "
            f"AND user_id IN ({ph}) AND event_type = 'subscribed' LIMIT 1",
            (otid, *ids))).fetchone()
        await db.execute(
            "UPDATE subscription_status SET user_id = ? WHERE original_transaction_id = ?",
            (to_user_id, otid))
        await db.execute(
            f"UPDATE subscription_events SET user_id = ? "
            f"WHERE original_transaction_id = ? AND user_id IN ({ph})",
            (to_user_id, otid, *ids))
        if already_subscribed:
            await db.execute(
                """UPDATE subscription_events SET event_type = 'reconciled', subtype = 'moved'
                   WHERE id = (SELECT id FROM subscription_events
                               WHERE user_id = ? AND original_transaction_id = ?
                                 AND source = 'verify_receipt' AND event_type = 'subscribed'
                               ORDER BY recorded_at DESC LIMIT 1)""",
                (to_user_id, otid))
    await db.commit()
    logger.info("plan_moved to=%s from=%s count=%d", to_user_id[:8],
                ",".join(h["id"][:8] for h in paid), len(paid))
    return True
