"""One-time IAP unlocks (non-consumable), Scott 2026-10-13.

Spec: SS docs/MODEL_UNLOCKS_IAP.md. Two packs, each a per-USER feature, not
a tier: unlock_apple_cloud and unlock_byok. A bundle grants both. The
"complete" product grants whichever pack the buyer does not own yet; App
Store cannot enforce that prerequisite, so GP applies Scott's ruling:

  owns exactly one pack -> grant the other
  owns none             -> pending; granted when a pack arrives
  owns both             -> nothing granted; refund_flagged (operator alert)

Everything here lives in the `purchases` table and never touches users.tier,
users.original_transaction_id (the ONE subscription slot) or the monthly
allocation. A refund of an unlock revokes the unlock and nothing else.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import aiosqlite
import yaml

logger = logging.getLogger(__name__)

UNLOCK_FEATURES = ("unlock_apple_cloud", "unlock_byok")
_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "unlocks.yml"


@lru_cache(maxsize=1)
def products() -> dict[str, dict]:
    try:
        return (yaml.safe_load(_CONFIG.read_text()) or {}).get("products") or {}
    except FileNotFoundError:
        return {}


def is_unlock_product(product_id: str | None) -> bool:
    return bool(product_id) and product_id in products()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def owned(db: aiosqlite.Connection, user_id: str) -> set[str]:
    """The unlock features this user holds right now (granted, not revoked)."""
    rows = await (await db.execute(
        "SELECT grants FROM purchases WHERE user_id = ? AND status = 'granted' AND revoked_at IS NULL",
        (user_id,))).fetchall()
    out: set[str] = set()
    for (g,) in rows:
        try:
            out.update(json.loads(g or "[]"))
        except ValueError:
            continue
    return out & set(UNLOCK_FEATURES)


async def features_for(db: aiosqlite.Connection, user_id: str) -> dict[str, str]:
    have = await owned(db, user_id)
    return {f: ("enabled" if f in have else "disabled") for f in UNLOCK_FEATURES}


async def _flag_refund(db, user_id: str, transaction_id: str, product_id: str) -> None:
    logger.warning("unlock_refund_flagged user=%s txn=%s product=%s", user_id, transaction_id, product_id)
    try:
        from app.config import get_settings
        from app.services.alerting import report_incident
        await report_incident(
            db, category="unlock_refund_flagged", subject=transaction_id,
            details={"user_id": user_id, "transaction_id": transaction_id, "product_id": product_id,
                     "meaning": "A 'complete' pack was bought by someone who already owns both "
                                "unlocks. Nothing was granted; refund it in App Store Connect."},
            from_addr=get_settings().alert_email_from)
    except Exception as e:  # alerting must never break a purchase
        logger.warning("unlock_refund_flagged alert failed (non-fatal): %s", e)


async def _resolve_completes(db, user_id: str) -> None:
    """Re-decide every pending "complete" row for this user against what they
    own now. Called after any grant, move or merge."""
    pending = await (await db.execute(
        "SELECT transaction_id, product_id FROM purchases WHERE user_id = ? AND status = 'pending' "
        "AND revoked_at IS NULL ORDER BY recorded_at", (user_id,))).fetchall()
    for txn, pid in pending:
        have = await owned(db, user_id)
        missing = [f for f in UNLOCK_FEATURES if f not in have]
        if len(missing) == 1:
            await db.execute("UPDATE purchases SET status = 'granted', grants = ? WHERE transaction_id = ?",
                             (json.dumps(missing), txn))
        elif not missing:
            await db.execute("UPDATE purchases SET status = 'refund_flagged' WHERE transaction_id = ?", (txn,))
            await _flag_refund(db, user_id, txn, pid)


async def record(
    db: aiosqlite.Connection, *, user_id: str, transaction_id: str, product_id: str,
    original_transaction_id: str | None = None, environment: str | None = None,
    purchased_at: str | None = None, source: str = "verify_receipt",
) -> dict:
    """Record one verified unlock transaction for `user_id`. Idempotent per
    transaction: a re-post (Restore, launch reconciliation, the webhook after
    the client) changes nothing, except that a transaction verified by a
    DIFFERENT account moves to it, as a subscription's plan does on restore.
    Returns {"status": granted|pending|refund_flagged, "unlocks": [...]}."""
    spec = products().get(product_id)
    if spec is None:
        raise ValueError(f"not an unlock product: {product_id}")
    if not transaction_id:
        # SQLite lets a TEXT primary key be NULL, so a missing id would
        # insert a fresh row on every Restore instead of being idempotent.
        raise ValueError("an unlock needs a transaction id")
    row = await (await db.execute(
        "SELECT user_id, status FROM purchases WHERE transaction_id = ?", (transaction_id,))).fetchone()
    if row is not None:
        prev_user, status = row
        if prev_user != user_id:
            await db.execute("UPDATE purchases SET user_id = ? WHERE transaction_id = ?", (user_id, transaction_id))
            await db.execute(
                "INSERT INTO account_links (from_user_id, to_user_id, reason, original_transaction_id, linked_at) "
                "VALUES (?, ?, 'restore', ?, ?)",
                (prev_user, user_id, original_transaction_id or transaction_id, _now()))
            logger.info("unlock_moved txn=%s from=%s to=%s", transaction_id, prev_user, user_id)
            await _resolve_completes(db, user_id)
            await db.commit()
            status = (await (await db.execute(
                "SELECT status FROM purchases WHERE transaction_id = ?", (transaction_id,))).fetchone())[0]
        return {"status": status, "unlocks": sorted(await owned(db, user_id))}

    if spec.get("kind") == "complete":
        have = await owned(db, user_id)
        missing = [f for f in UNLOCK_FEATURES if f not in have]
        if len(missing) == 1:
            status, grants = "granted", missing
        elif len(missing) == 2:
            status, grants = "pending", []
        else:
            status, grants = "refund_flagged", []
    else:
        status, grants = "granted", [g for g in spec.get("grants") or [] if g in UNLOCK_FEATURES]

    await db.execute(
        """INSERT INTO purchases (transaction_id, original_transaction_id, user_id, product_id, grants,
               status, environment, purchased_at, recorded_at, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (transaction_id, original_transaction_id, user_id, product_id, json.dumps(grants),
         status, environment, purchased_at, _now(), source))
    if status == "refund_flagged":
        await _flag_refund(db, user_id, transaction_id, product_id)
    if status == "granted":
        await _resolve_completes(db, user_id)
    await db.commit()
    logger.info("unlock_recorded user=%s txn=%s product=%s status=%s", user_id, transaction_id, product_id, status)
    return {"status": status, "unlocks": sorted(await owned(db, user_id))}


async def revoke(db: aiosqlite.Connection, *, transaction_id: str | None,
                 original_transaction_id: str | None, reason: str) -> list[str]:
    """Revoke the unlock row(s) for a refunded or revoked transaction. Returns
    the user ids touched. Never touches the user's tier or subscription."""
    keys = [k for k in (transaction_id, original_transaction_id) if k]
    if not keys:
        return []
    rows = await (await db.execute(
        f"SELECT transaction_id, user_id FROM purchases WHERE revoked_at IS NULL AND "
        f"(transaction_id IN ({','.join('?' * len(keys))}) OR original_transaction_id IN ({','.join('?' * len(keys))}))",
        (*keys, *keys))).fetchall()
    for txn, _ in rows:
        await db.execute("UPDATE purchases SET revoked_at = ?, revoke_reason = ? WHERE transaction_id = ?",
                         (_now(), reason, txn))
    await db.commit()
    return sorted({u for _, u in rows})


async def find_user(db: aiosqlite.Connection, *, transaction_id: str | None,
                    original_transaction_id: str | None, app_account_token: str | None) -> str | None:
    """Who owns an unlock transaction: the purchases row first, then the
    appAccountToken (SS sets it to the user id, upper-case from Swift)."""
    for k in (transaction_id, original_transaction_id):
        if k:
            r = await (await db.execute(
                "SELECT user_id FROM purchases WHERE transaction_id = ? OR original_transaction_id = ? LIMIT 1",
                (k, k))).fetchone()
            if r:
                return r[0]
    if isinstance(app_account_token, str) and app_account_token:
        r = await (await db.execute("SELECT id FROM users WHERE id = ?", (app_account_token.lower(),))).fetchone()
        if r:
            return r[0]
    return None


