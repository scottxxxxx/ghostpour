"""One-time IAP unlocks (non-consumable), Scott 2026-10-13.

Spec: SS docs/MODEL_UNLOCKS_IAP.md. Two packs, each a per-USER feature, not
a tier: unlock_apple_cloud and unlock_byok. A bundle grants both. Two $4.99
upgrades (Scott, 2026-10-13, replacing one "complete" product) each grant
one pack to an account that owns the OTHER; App Store cannot enforce that
prerequisite, so GP decides when the transaction arrives:

  owns the required pack, not the granted one -> granted
  owns the granted pack already               -> refund_flagged (operator alert)
  does not own the required pack              -> pending; granted when it arrives
                                                 (it was bought on an unmerged
                                                 account, and Apple has the money)

Everything here lives in the `purchases` table and never touches users.tier,
users.original_transaction_id (the ONE subscription slot) or the monthly
allocation. A refund of an unlock revokes the unlock and nothing else.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
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


def _decide(spec: dict, have: set[str]) -> tuple[str, list[str]]:
    """(status, grants) for a product spec against what the user owns now."""
    grants = [g for g in spec.get("grants") or [] if g in UNLOCK_FEATURES]
    required = spec.get("requires")
    if required is None:
        return "granted", grants
    if all(g in have for g in grants):
        return "refund_flagged", []
    if required not in have:
        return "pending", []
    return "granted", grants


async def _flag_refund(db, user_id: str, transaction_id: str, product_id: str) -> None:
    logger.warning("unlock_refund_flagged user=%s txn=%s product=%s", user_id, transaction_id, product_id)
    try:
        from app.config import get_settings
        from app.services.alerting import report_incident
        await report_incident(
            db, category="unlock_refund_flagged", subject=transaction_id,
            details={"user_id": user_id, "transaction_id": transaction_id, "product_id": product_id,
                     "meaning": "An upgrade was bought for a pack this account already owns. "
                                "Nothing was granted; refund it in App Store Connect."},
            from_addr=get_settings().alert_email_from)
    except Exception as e:  # alerting must never break a purchase
        logger.warning("unlock_refund_flagged alert failed (non-fatal): %s", e)


async def _resolve_pending(db, user_id: str) -> None:
    """Re-decide every pending upgrade for this user against what they own
    now. Called after any grant, move or merge."""
    pending = await (await db.execute(
        "SELECT transaction_id, product_id FROM purchases WHERE user_id = ? AND status = 'pending' "
        "AND revoked_at IS NULL ORDER BY recorded_at", (user_id,))).fetchall()
    for txn, pid in pending:
        spec = products().get(pid)
        if spec is None:
            continue
        status, grants = _decide(spec, await owned(db, user_id))
        if status == "granted":
            await db.execute("UPDATE purchases SET status = 'granted', grants = ? WHERE transaction_id = ?",
                             (json.dumps(grants), txn))
        elif status == "refund_flagged":
            await db.execute("UPDATE purchases SET status = 'refund_flagged' WHERE transaction_id = ?", (txn,))
            await _flag_refund(db, user_id, txn, pid)

async def record(
    db: aiosqlite.Connection, *, user_id: str, transaction_id: str, product_id: str,
    original_transaction_id: str | None = None, environment: str | None = None,
    purchased_at: str | None = None, source: str = "verify_receipt",
    price_paid: float | None = None, currency: str | None = None,
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
        if price_paid is not None:
            # The client usually posts first; the money may arrive with the webhook.
            await db.execute(
                "UPDATE purchases SET price_paid = ?, currency = ? WHERE transaction_id = ? AND price_paid IS NULL",
                (price_paid, currency, transaction_id))
            await db.commit()
        if prev_user != user_id:
            await db.execute("UPDATE purchases SET user_id = ? WHERE transaction_id = ?", (user_id, transaction_id))
            await db.execute(
                "INSERT INTO account_links (from_user_id, to_user_id, reason, original_transaction_id, linked_at) "
                "VALUES (?, ?, 'restore', ?, ?)",
                (prev_user, user_id, original_transaction_id or transaction_id, _now()))
            logger.info("unlock_moved txn=%s from=%s to=%s", transaction_id, prev_user, user_id)
            await _resolve_pending(db, user_id)
            await db.commit()
            status = (await (await db.execute(
                "SELECT status FROM purchases WHERE transaction_id = ?", (transaction_id,))).fetchone())[0]
        return {"status": status, "unlocks": sorted(await owned(db, user_id))}

    status, grants = _decide(spec, await owned(db, user_id))

    await db.execute(
        """INSERT INTO purchases (transaction_id, original_transaction_id, user_id, product_id, grants,
               status, environment, purchased_at, recorded_at, source, price_paid, currency)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (transaction_id, original_transaction_id, user_id, product_id, json.dumps(grants),
         status, environment, purchased_at, _now(), source, price_paid, currency))
    if status == "refund_flagged":
        await _flag_refund(db, user_id, transaction_id, product_id)
    if status == "granted":
        await _resolve_pending(db, user_id)
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




async def report(db: aiosqlite.Connection, days: int = 365) -> dict:
    """One-time unlock purchases for the dashboard (Scott, 2026-10-22): counts
    per product and environment, money by currency, and the newest rows.
    Money is Production only; Sandbox (TestFlight, review) is counted beside
    it, never inside it, as the subscription cards do."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = await db.execute(
        "SELECT p.transaction_id, p.user_id, u.email, p.product_id, p.status, p.environment, "
        "p.purchased_at, p.recorded_at, p.revoked_at, p.revoke_reason, p.source, p.price_paid, p.currency "
        "FROM purchases p LEFT JOIN users u ON u.id = p.user_id "
        "WHERE COALESCE(p.purchased_at, p.recorded_at) >= ? "
        "ORDER BY COALESCE(p.purchased_at, p.recorded_at) DESC", (since,))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in await cur.fetchall()]
    by_product: dict[str, dict] = {}
    revenue: dict[str, float] = {}
    totals = {"production": 0, "sandbox": 0, "other": 0, "refunded": 0, "pending": 0, "refund_flagged": 0}
    for r in rows:
        env = (r["environment"] or "").lower()
        env = env if env in ("production", "sandbox") else "other"
        b = by_product.setdefault(r["product_id"], {"product_id": r["product_id"], "production": 0,
                                                    "sandbox": 0, "other": 0, "refunded": 0})
        b[env] += 1
        totals[env] += 1
        if r["revoked_at"]:
            b["refunded"] += 1
            totals["refunded"] += 1
        if r["status"] in ("pending", "refund_flagged"):
            totals[r["status"]] += 1
        if env == "production" and not r["revoked_at"] and r["price_paid"] and r["currency"]:
            revenue[r["currency"]] = round(revenue.get(r["currency"], 0) + r["price_paid"], 2)
    return {"days": days, "totals": totals, "revenue_production": revenue,
            "by_product": sorted(by_product.values(), key=lambda b: b["product_id"]),
            "recent": rows[:100]}
