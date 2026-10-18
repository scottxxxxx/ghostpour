"""Record-only purchase ledger for apps whose pay sheet is client-gated
(Scott, 2026-10-18: N-400 purchase stats with no N-400 code change).

N-400 sells one consumable per application and decides on the phone what it
unlocks, so no purchase ever reached GP. Apple's App Store Server
Notifications do, once the app's notification URL points here: every
ONE_TIME_CHARGE and REFUND, signed by Apple, with the time, price and
storefront. This module writes them down and nothing else. It never touches
users, tiers, grants or `purchases` (SS's unlocks).

What a row can NOT say is who bought. The client's appAccountToken is a hash
of the applicant's A-Number, and the A-Number is masked before it leaves the
phone, so GP holds nothing that token can be matched to. The same token on
two rows is the same A-Number, and that is all it says. Which case a purchase
belongs to is inferred in `n400_stats`, and labelled there as inferred.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)


def ledger_bundles() -> dict[str, str]:
    """{bundle_id: app_id} for every app that opted in with
    `purchase_ledger: true` in apps.yml."""
    from app.routers.config import load_apps
    out: dict[str, str] = {}
    for app_id, spec in (load_apps().get("apps") or {}).items():
        if isinstance(spec, dict) and spec.get("purchase_ledger") and spec.get("bundle_id"):
            out[str(spec["bundle_id"])] = app_id
    return out


def _ms_to_iso(ms) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


async def record_notification(db: aiosqlite.Connection, *, app_id: str, bundle_id: str,
                              notification_type: str, transaction_info: dict,
                              environment: str | None) -> str:
    """Write one Apple notification into app_purchases. Returns the action
    taken: recorded | duplicate | revoked | none."""
    txn = transaction_info.get("transactionId")
    if notification_type == "ONE_TIME_CHARGE":
        if not txn:
            # A NULL TEXT primary key would insert a fresh row on every retry.
            logger.warning("app_purchase with no transactionId app=%s", app_id)
            return "none"
        cur = await db.execute(
            """INSERT OR IGNORE INTO app_purchases
                 (transaction_id, original_transaction_id, app_id, bundle_id, product_id,
                  app_account_token, purchased_at, price_milli, currency, storefront,
                  environment, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (txn, transaction_info.get("originalTransactionId"), app_id, bundle_id,
             transaction_info.get("productId"),
             (transaction_info.get("appAccountToken") or "").lower() or None,
             _ms_to_iso(transaction_info.get("purchaseDate")),
             transaction_info.get("price"), transaction_info.get("currency"),
             transaction_info.get("storefront"),
             transaction_info.get("environment") or environment,
             datetime.now(timezone.utc).isoformat()))
        await db.commit()
        action = "recorded" if cur.rowcount else "duplicate"
        logger.info("app_purchase_%s app=%s txn=%s product=%s",
                    action, app_id, txn, transaction_info.get("productId"))
        return action
    if notification_type in ("REFUND", "REVOKE"):
        keys = [k for k in (txn, transaction_info.get("originalTransactionId")) if k]
        if not keys:
            return "none"
        marks = ",".join("?" * len(keys))
        when = (_ms_to_iso(transaction_info.get("revocationDate"))
                or datetime.now(timezone.utc).isoformat())
        cur = await db.execute(
            f"UPDATE app_purchases SET revoked_at = ?, revoke_reason = ? "
            f"WHERE revoked_at IS NULL AND (transaction_id IN ({marks}) "
            f"OR original_transaction_id IN ({marks}))",
            (when, notification_type.lower(), *keys, *keys))
        await db.commit()
        logger.info("app_purchase_revoked app=%s txn=%s rows=%s", app_id, txn, cur.rowcount)
        return "revoked"
    return "none"
