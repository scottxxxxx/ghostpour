"""App Store Server API client (outbound, JWT-signed).

Apple is the system of record for whether a subscription is actually active.
The Server Notifications webhook is a push stream and push streams drop, so we
PULL from Apple here for two jobs: verifying a transaction at signup, and the
periodic reconciliation sweep that catches missed/stale notifications.

DORMANT by default. `is_configured()` is False until the issuer id / key id /
.p8 private key are provisioned (env or Secret Manager), so nothing here runs
or fails on an un-provisioned deploy. TestFlight uses the Sandbox base URL.

Docs: https://developer.apple.com/documentation/appstoreserverapi
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone

import httpx
import jwt

from app.config import get_settings
from app.services.apple_notifications import AppleJWSError, decode_and_verify_jws

logger = logging.getLogger("ghostpour.app_store_server_api")

_PROD_BASE = "https://api.storekit.itunes.apple.com"
_SANDBOX_BASE = "https://api.storekit-sandbox.itunes.apple.com"

# Apple subscription status codes (Get All Subscription Statuses).
_STATUS_ACTIVE = 1
_STATUS_EXPIRED = 2
_STATUS_BILLING_RETRY = 3
_STATUS_GRACE = 4
_STATUS_REVOKED = 5
# Statuses where the customer still has entitlement.
_ENTITLED_STATUSES = {_STATUS_ACTIVE, _STATUS_GRACE}


def is_configured() -> bool:
    s = get_settings()
    return bool(s.app_store_issuer_id and s.app_store_key_id and s.app_store_private_key_b64)


def _base_url() -> str:
    return _SANDBOX_BASE if get_settings().app_store_environment != "Production" else _PROD_BASE


def _private_key_pem() -> bytes:
    """The .p8 is stored base64-encoded; decode to the PEM bytes PyJWT wants."""
    raw = get_settings().app_store_private_key_b64.strip()
    return base64.b64decode(raw)


def _signed_jwt() -> str:
    """ES256 bearer token for the Server API (valid ~20 min)."""
    s = get_settings()
    now = int(time.time())
    headers = {"alg": "ES256", "kid": s.app_store_key_id, "typ": "JWT"}
    payload = {
        "iss": s.app_store_issuer_id,
        "iat": now,
        "exp": now + 1200,
        "aud": "appstoreconnect-v1",
        "bid": s.apple_bundle_id,
    }
    return jwt.encode(payload, _private_key_pem(), algorithm="ES256", headers=headers)


def _product_to_tier() -> dict[str, str]:
    """Same mapping the webhook uses, rebuilt from tier config."""
    from app.models.tier import load_tier_config
    try:
        tier_config = load_tier_config(get_settings().tier_config_path)
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    for name, tier in tier_config.tiers.items():
        for pid in tier.all_product_ids.values():
            if pid:
                mapping[pid] = name
    return mapping


def _ms_to_iso(ms) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


async def get_subscription_state(original_transaction_id: str) -> dict | None:
    """Pull Apple's authoritative state for a subscription.

    Returns a normalized dict, or None if not configured / not found / on error
    (callers treat None as "couldn't verify" and leave local state untouched):

        {
          "entitled": bool,        # active or in grace period
          "status": int,           # Apple's raw status code
          "tier": str | None,      # mapped from productId, None if unknown
          "product_id": str | None,
          "expires_at": str | None,
          "environment": str,
          "original_transaction_id": str,
        }
    """
    if not is_configured():
        return None
    url = f"{_base_url()}/inApps/v1/subscriptions/{original_transaction_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {_signed_jwt()}"})
    except Exception as e:
        logger.warning("App Store Server API request failed for %s: %s", original_transaction_id, e)
        return None
    if resp.status_code == 404:
        return None  # Apple has no record of this transaction id
    if resp.status_code != 200:
        logger.warning(
            "App Store Server API %s for %s: %s",
            resp.status_code, original_transaction_id, resp.text[:200],
        )
        return None

    body = resp.json()
    environment = body.get("environment", get_settings().app_store_environment)
    # Find the most relevant lastTransaction across subscription groups.
    best = None
    for group in body.get("data", []):
        for last in group.get("lastTransactions", []):
            status = last.get("status")
            signed = last.get("signedTransactionInfo")
            txn = {}
            if isinstance(signed, str):
                try:
                    txn = decode_and_verify_jws(signed, get_settings().apple_bundle_id)
                except AppleJWSError as e:
                    logger.warning("Could not decode signedTransactionInfo: %s", e)
            cand = {
                "status": status,
                "product_id": txn.get("productId"),
                "expires_at": _ms_to_iso(txn.get("expiresDate")),
                "original_purchase_date": _ms_to_iso(txn.get("originalPurchaseDate")),
            }
            # Prefer an entitled status if any group is active.
            if best is None or (status in _ENTITLED_STATUSES and best["status"] not in _ENTITLED_STATUSES):
                best = cand
    if best is None:
        return None
    tier = _product_to_tier().get(best.get("product_id") or "")
    return {
        "entitled": best["status"] in _ENTITLED_STATUSES,
        "status": best["status"],
        "tier": tier,
        "product_id": best.get("product_id"),
        "expires_at": best.get("expires_at"),
        "original_purchase_date": best.get("original_purchase_date"),
        "environment": environment,
        "original_transaction_id": original_transaction_id,
    }
