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
                    verified: bool, tier_config) -> bool:
    """Downgrade the paid holders and carry their period's usage to the
    requester. True when a plan moved. Call AFTER the requester's plan is
    written, since that write resets usage."""
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
    for h in paid:
        await _downgrade_to_free(db, h["id"], tier_config)
    await db.commit()
    logger.info("plan_moved to=%s from=%s count=%d", to_user_id[:8],
                ",".join(h["id"][:8] for h in paid), len(paid))
    return True
