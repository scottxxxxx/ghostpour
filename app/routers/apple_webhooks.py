"""Apple App Store Server Notifications V2 webhook.

Receives signed notifications from Apple about subscription lifecycle events
(renewals, cancellations, refunds, billing failures, etc.) and updates user
tiers accordingly.

This endpoint is unauthenticated — Apple POSTs to it directly. Security comes
from JWS signature verification against Apple's certificate chain.

See: https://developer.apple.com/documentation/appstoreservernotifications
"""

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.services.apple_notifications import (
    AppleJWSError,
    decode_notification,
)

logger = logging.getLogger("ghostpour.apple_webhooks")

router = APIRouter()


class AppleNotificationRequest(BaseModel):
    signedPayload: str


# Notification types we handle
_UPGRADE_TYPES = {"SUBSCRIBED", "DID_RENEW"}
_DOWNGRADE_TYPES = {"EXPIRED", "REVOKE", "GRACE_PERIOD_EXPIRED"}
_REFUND_TYPES = {"REFUND"}
_BILLING_RETRY_TYPES = {"DID_FAIL_TO_RENEW"}


def _build_product_to_tier(tier_config) -> dict[str, str]:
    """Build a product_id → tier_name mapping from tier config."""
    mapping = {}
    for name, tier in tier_config.tiers.items():
        for product_id in tier.all_product_ids.values():
            if product_id:
                mapping[product_id] = name
    return mapping


async def _get_user_by_apple_sub(db: aiosqlite.Connection, apple_sub: str) -> dict | None:
    """Look up a user by their Apple subscription original_transaction_id or appAccountToken."""
    # First try apple_sub (Apple's 'sub' claim from Sign In with Apple)
    cursor = await db.execute(
        "SELECT id, tier, is_trial, monthly_used_usd FROM users WHERE apple_sub = ?",
        (apple_sub,),
    )
    row = await cursor.fetchone()
    if row:
        return {"id": row[0], "tier": row[1], "is_trial": row[2], "monthly_used_usd": row[3]}
    return None


async def _downgrade_to_free(db: aiosqlite.Connection, user_id: str, tier_config) -> str:
    """Downgrade a user to the free tier."""
    free_tier = tier_config.tiers.get("free")
    free_limit = free_tier.monthly_cost_limit_usd if free_tier else 0.05
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """UPDATE users SET
            tier = 'free',
            monthly_cost_limit_usd = ?,
            monthly_used_usd = ?,
            overage_balance_usd = 0,
            is_trial = 0,
            trial_start = NULL,
            trial_end = NULL,
            simulated_tier = NULL,
            simulated_exhausted = 0,
            updated_at = ?
           WHERE id = ?""",
        (free_limit, free_limit, now, user_id),
    )
    await db.commit()
    return "free"


async def _upgrade_to_tier(
    db: aiosqlite.Connection, user_id: str, tier_name: str, tier_config
) -> str:
    """Upgrade/set a user to a specific tier."""
    tier = tier_config.tiers.get(tier_name)
    if not tier:
        logger.error("Unknown tier %s for upgrade", tier_name)
        return tier_name

    now = datetime.now(timezone.utc)
    resets_at = (now + timedelta(days=30)).isoformat()

    await db.execute(
        """UPDATE users SET
            tier = ?,
            monthly_cost_limit_usd = ?,
            monthly_used_usd = 0,
            overage_balance_usd = 0,
            allocation_resets_at = ?,
            is_trial = 0,
            trial_start = NULL,
            trial_end = NULL,
            simulated_tier = NULL,
            simulated_exhausted = 0,
            updated_at = ?
           WHERE id = ?""",
        (tier_name, tier.monthly_cost_limit_usd, resets_at, now.isoformat(), user_id),
    )
    await db.commit()
    return tier_name


@router.post("/apple-notifications")
async def apple_notifications(
    body: AppleNotificationRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Receive and process Apple App Store Server Notifications V2.

    Apple POSTs a signed JWS payload containing subscription lifecycle events.
    We verify the signature, decode the notification, and update the user's
    tier in the database.
    """
    settings = get_settings()

    # Verify and decode the JWS
    try:
        notification = decode_notification(body.signedPayload, settings.apple_bundle_id)
    except AppleJWSError as e:
        logger.warning("Apple notification JWS verification failed: %s", e)
        return JSONResponse(status_code=400, content={"error": f"JWS verification failed: {e}"})

    notification_type = notification.get("notificationType", "")
    subtype = notification.get("subtype", "")
    data = notification.get("data", {})
    transaction_info = data.get("signedTransactionInfo", {})

    # Log the notification
    product_id = transaction_info.get("productId", "unknown") if isinstance(transaction_info, dict) else "undecoded"
    logger.info(
        "Apple notification: type=%s subtype=%s product=%s",
        notification_type, subtype or "none", product_id,
    )

    # If signedTransactionInfo wasn't decoded (verification failed), we can't proceed
    if not isinstance(transaction_info, dict):
        logger.error(
            "Cannot process notification: signedTransactionInfo not decoded (type=%s)",
            notification_type,
        )
        # Still return 200 so Apple doesn't retry
        return {"status": "received", "action": "skipped", "reason": "transaction_info_not_decoded"}

    # Find the user by originalTransactionId (stored during /v1/verify-receipt).
    # Future: also try appAccountToken once SS sets it during purchases.
    original_transaction_id = transaction_info.get("originalTransactionId")
    app_account_token = transaction_info.get("appAccountToken")

    tier_config = request.app.state.tier_config

    user = None

    # Primary lookup: originalTransactionId stored in users table
    if original_transaction_id:
        cursor = await db.execute(
            "SELECT id, tier, is_trial, monthly_used_usd FROM users WHERE original_transaction_id = ?",
            (original_transaction_id,),
        )
        row = await cursor.fetchone()
        if row:
            user = {"id": row[0], "tier": row[1], "is_trial": row[2], "monthly_used_usd": row[3]}

    # Fallback: appAccountToken maps to user ID (future, once SS sets it)
    if not user and app_account_token:
        cursor = await db.execute(
            "SELECT id, tier, is_trial, monthly_used_usd FROM users WHERE id = ?",
            (app_account_token,),
        )
        row = await cursor.fetchone()
        if row:
            user = {"id": row[0], "tier": row[1], "is_trial": row[2], "monthly_used_usd": row[3]}

    if not user:
        logger.warning(
            "Apple notification: no user found for originalTransactionId=%s appAccountToken=%s (type=%s)",
            original_transaction_id, app_account_token, notification_type,
        )
        return {"status": "received", "action": "skipped", "reason": "user_not_found"}

    user_id = user["id"]
    old_tier = user["tier"]

    # Handle notification by type
    if notification_type in _UPGRADE_TYPES:
        # SUBSCRIBED or DID_RENEW — set tier based on product_id
        product_to_tier = _build_product_to_tier(tier_config)
        new_tier_name = product_to_tier.get(transaction_info.get("productId", ""))

        if not new_tier_name:
            logger.warning(
                "Apple notification: unknown product %s for user %s",
                transaction_info.get("productId"), user_id,
            )
            return {"status": "received", "action": "skipped", "reason": "unknown_product"}

        if old_tier == new_tier_name and not user["is_trial"]:
            # DID_RENEW for same tier — just log it
            logger.info("Apple notification: renewal confirmed for user %s (tier=%s)", user_id, old_tier)
            return {"status": "received", "action": "none", "tier": old_tier}

        new_tier = await _upgrade_to_tier(db, user_id, new_tier_name, tier_config)
        logger.info(
            "Apple notification: upgraded user %s from %s to %s (type=%s)",
            user_id, old_tier, new_tier, notification_type,
        )
        return {"status": "received", "action": "upgraded", "old_tier": old_tier, "new_tier": new_tier}

    elif notification_type in _DOWNGRADE_TYPES:
        # EXPIRED, REVOKE, GRACE_PERIOD_EXPIRED — downgrade to free
        if old_tier == "free":
            return {"status": "received", "action": "none", "tier": "free"}

        new_tier = await _downgrade_to_free(db, user_id, tier_config)
        logger.info(
            "Apple notification: downgraded user %s from %s to free (type=%s subtype=%s)",
            user_id, old_tier, notification_type, subtype,
        )
        return {"status": "received", "action": "downgraded", "old_tier": old_tier, "new_tier": new_tier}

    elif notification_type in _REFUND_TYPES:
        # REFUND — downgrade to free immediately
        if old_tier == "free":
            return {"status": "received", "action": "none", "tier": "free"}

        new_tier = await _downgrade_to_free(db, user_id, tier_config)
        logger.info(
            "Apple notification: refund for user %s, downgraded from %s to free",
            user_id, old_tier,
        )
        return {"status": "received", "action": "refunded", "old_tier": old_tier, "new_tier": new_tier}

    elif notification_type in _BILLING_RETRY_TYPES:
        # DID_FAIL_TO_RENEW — billing issue, log but don't downgrade yet
        # Apple retries billing for up to 60 days. The user keeps access
        # during the grace period. We'll downgrade on GRACE_PERIOD_EXPIRED
        # or EXPIRED if billing never succeeds.
        logger.warning(
            "Apple notification: billing failed for user %s (tier=%s, subtype=%s). "
            "Keeping tier active during retry period.",
            user_id, old_tier, subtype,
        )
        return {"status": "received", "action": "billing_retry", "tier": old_tier}

    elif notification_type == "TEST":
        logger.info("Apple notification: test notification received")
        return {"status": "received", "action": "test"}

    else:
        # Other types (DID_CHANGE_RENEWAL_PREF, PRICE_INCREASE, etc.)
        # Log but take no action
        logger.info(
            "Apple notification: unhandled type=%s subtype=%s for user %s",
            notification_type, subtype, user_id,
        )
        return {"status": "received", "action": "none", "type": notification_type}
