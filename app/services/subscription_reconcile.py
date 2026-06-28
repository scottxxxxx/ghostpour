"""Subscription reconciliation sweep.

Notifications for speed, the Server API for correctness. ASSN is a push stream
and push streams drop (endpoint down during a deploy, retries aged out, a
malformed payload), so we periodically pull Apple's authoritative state and
fix any drift in BOTH directions: a user paying Apple we still think is free,
or a user Apple shows lapsed/refunded that we still think is paid.

DORMANT unless `subscription_reconcile_enabled` is set AND the Server API is
configured. Fail-soft: a failed user or tick never kills the loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

from app.config import get_settings
from app.models.tier import load_tier_config
from app.services import app_store_server_api as assa
from app.services import subscriptions as subs

logger = logging.getLogger("ghostpour.subscription_reconcile")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _apply_tier(db: aiosqlite.Connection, user_id: str, tier_name: str, tier_config) -> None:
    """Set a user's current tier + matching budget limit. Reconciliation only
    corrects the tier/limit; allocation timing is left to the normal renewal
    path so a reconcile doesn't reset someone's billing window."""
    tier = tier_config.tiers.get(tier_name)
    limit = tier.monthly_cost_limit_usd if tier else None
    if tier_name == "free":
        await db.execute(
            """UPDATE users SET tier='free', monthly_cost_limit_usd=?,
                is_trial=0, trial_start=NULL, trial_end=NULL,
                simulated_tier=NULL, simulated_exhausted=0, updated_at=?
               WHERE id=?""",
            (limit, _now_iso(), user_id),
        )
    else:
        await db.execute(
            "UPDATE users SET tier=?, monthly_cost_limit_usd=?, updated_at=? WHERE id=?",
            (tier_name, limit, _now_iso(), user_id),
        )


async def reconcile_user(db: aiosqlite.Connection, user_row, tier_config) -> str | None:
    """Reconcile one user against Apple. Returns a "from->to" string if it fixed
    drift, else None (in sync, unverifiable, or unknown product)."""
    otid = user_row["original_transaction_id"]
    if not otid:
        return None
    state = await assa.get_subscription_state(otid)
    if state is None:
        return None  # couldn't verify — leave local state untouched
    cur_tier = user_row["tier"]
    if state["entitled"]:
        apple_tier = state["tier"]
        if apple_tier is None:
            return None  # entitled but product not mapped to a tier — don't guess
    else:
        apple_tier = "free"
    if apple_tier == cur_tier:
        return None  # in sync

    await _apply_tier(db, user_row["id"], apple_tier, tier_config)
    await subs.record_subscription_event(
        db, user_id=user_row["id"], event_type="reconciled", subtype="drift_fix",
        from_tier=cur_tier, to_tier=apple_tier,
        product_id=state.get("product_id"),
        original_transaction_id=otid,
        expires_at=state.get("expires_at"),
        environment=state.get("environment"),
        source="reconciliation",
    )
    logger.info("reconcile fixed user=%s %s->%s", user_row["id"], cur_tier, apple_tier)
    return f"{cur_tier}->{apple_tier}"


async def sweep(db: aiosqlite.Connection) -> dict:
    """Reconcile every user that has an Apple transaction id. Returns a summary
    {checked, fixed, fixes:[...]}."""
    if not assa.is_configured():
        return {"checked": 0, "fixed": 0, "fixes": [], "skipped": "not_configured"}
    tier_config = load_tier_config(get_settings().tier_config_path)
    rows = await (await db.execute(
        "SELECT id, tier, original_transaction_id FROM users "
        "WHERE original_transaction_id IS NOT NULL AND original_transaction_id != ''"
    )).fetchall()
    fixes = []
    for r in rows:
        try:
            res = await reconcile_user(db, r, tier_config)
            if res:
                fixes.append({"user_id": r["id"], "change": res})
        except Exception as e:
            logger.warning("reconcile_user failed for %s: %s", r["id"], e)
    return {"checked": len(rows), "fixed": len(fixes), "fixes": fixes}


async def run_daemon(app) -> None:
    """Lifespan-spawned loop. Dormant unless enabled + Server API configured.
    First sweep after a short delay so it doesn't tangle with startup."""
    settings = app.state.settings
    if not settings.subscription_reconcile_enabled:
        logger.info("subscription_reconcile disabled — daemon not running")
        return
    await asyncio.sleep(30.0)
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    while True:
        try:
            if assa.is_configured():
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    res = await sweep(db)
                if res.get("fixed"):
                    logger.info("subscription_reconcile fixed %d of %d", res["fixed"], res["checked"])
        except Exception as e:
            logger.warning("subscription_reconcile tick failed: %s", e)
        try:
            await asyncio.sleep(app.state.settings.subscription_reconcile_interval_seconds)
        except asyncio.CancelledError:
            return
