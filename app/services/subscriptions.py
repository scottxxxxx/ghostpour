"""Subscription history: the append-only bookkeeping log + reporting.

`users.tier` holds only the *current* tier. This module records every
subscription lifecycle transition into `subscription_events` (the system of
record) and keeps two denormalized caches on the user row — `ever_subscribed`
and `first_subscribed_at` — for the hot path (offer-code "never subscribed"
targeting) and fast dashboard reads.

Writers: the Apple Server Notifications webhook (app/routers/apple_webhooks.py),
the /v1/verify-receipt path (app/routers/chat.py), and the reconciliation sweep
(app/services/subscription_reconcile.py). Readers: the admin Subscriptions
dashboard endpoints and promo targeting.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.subscriptions")

# List price per paid tier (what the customer pays Apple), for the bookkeeping
# report. This is the subscription price, NOT the tiers.yml budget cap. Apple
# remits proceeds after its commission; NET_FACTOR is the small-business 15%.
TIER_PRICE_USD: dict[str, float] = {"plus": 9.99, "pro": 14.99}
APPLE_NET_FACTOR = 0.85  # proceeds after Apple's 15% commission

# Normalized event types we record. Raw Apple notificationType is kept alongside.
PAID_EVENT_TYPES = {"subscribed", "renewed", "upgraded"}
# Event types that establish "this user has paid at some point".
_MARKS_EVER_SUBSCRIBED = PAID_EVENT_TYPES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_paid_tier(tier: str | None) -> bool:
    return bool(tier) and tier not in ("free", "")


def price_for_tier(tier: str | None) -> float | None:
    if not tier:
        return None
    return TIER_PRICE_USD.get(tier)


async def mark_ever_subscribed(
    db: aiosqlite.Connection, user_id: str, when: str | None = None, commit: bool = True
) -> None:
    """Set the ever_subscribed / first_subscribed_at caches for a user Apple
    confirms has subscribed (now or in the past). `ever_subscribed` is sticky
    once set; `first_subscribed_at` only moves earlier and is only written when
    we actually have a date (`when`), so an undated mark never overwrites a known
    first-subscribed timestamp with a wrong/now value."""
    if when:
        await db.execute(
            """UPDATE users SET
                ever_subscribed = 1,
                first_subscribed_at = CASE
                    WHEN first_subscribed_at IS NULL OR ? < first_subscribed_at
                    THEN ? ELSE first_subscribed_at END
               WHERE id = ?""",
            (when, when, user_id),
        )
    else:
        await db.execute("UPDATE users SET ever_subscribed = 1 WHERE id = ?", (user_id,))
    if commit:
        await db.commit()


async def record_subscription_event(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    event_type: str,
    to_tier: str | None,
    from_tier: str | None = None,
    notification_type: str | None = None,
    subtype: str | None = None,
    product_id: str | None = None,
    original_transaction_id: str | None = None,
    transaction_id: str | None = None,
    expires_at: str | None = None,
    environment: str | None = None,
    source: str = "assn",
    price_usd: float | None = None,
    effective_at: str | None = None,
    raw: dict | None = None,
    offer_id: str | None = None,
    commit: bool = True,
) -> str:
    """Append one subscription event and keep the user-row caches in lockstep.

    Idempotent on the caller's side via `transaction_id`/`effective_at` if they
    choose to dedup; this function always inserts (the log is append-only). The
    `ever_subscribed` / `first_subscribed_at` caches advance monotonically: once
    set they only move earlier, never cleared by a downgrade.
    """
    eff = effective_at or _now_iso()
    if price_usd is None and event_type in PAID_EVENT_TYPES:
        price_usd = price_for_tier(to_tier)
    event_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO subscription_events
            (id, user_id, event_type, notification_type, subtype, from_tier,
             to_tier, product_id, original_transaction_id, transaction_id,
             expires_at, environment, source, price_usd, effective_at,
             recorded_at, raw, offer_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, user_id, event_type, notification_type, subtype, from_tier,
            to_tier, product_id, original_transaction_id, transaction_id,
            expires_at, environment, source, price_usd, eff,
            _now_iso(), json.dumps(raw) if raw is not None else None, offer_id,
        ),
    )
    # Advance the caches when this event marks a paid state.
    if event_type in _MARKS_EVER_SUBSCRIBED or is_paid_tier(to_tier):
        await mark_ever_subscribed(db, user_id, when=eff, commit=False)
    if commit:
        await db.commit()
    logger.info(
        "subscription_event user=%s type=%s %s->%s source=%s",
        user_id, event_type, from_tier, to_tier, source,
    )
    return event_id


# ---------------------------------------------------------------------------
# Reporting (admin dashboard)
# ---------------------------------------------------------------------------

async def summary(db: aiosqlite.Connection) -> dict:
    """Top-line counts for the Subscriptions tab header."""
    row = await (await db.execute(
        "SELECT COUNT(*) AS users, "
        "SUM(CASE WHEN ever_subscribed = 1 THEN 1 ELSE 0 END) AS ever, "
        "SUM(CASE WHEN tier NOT IN ('free','') THEN 1 ELSE 0 END) AS paid_now "
        "FROM users"
    )).fetchone()
    by_tier = {}
    async with db.execute(
        "SELECT tier, COUNT(*) AS n FROM users WHERE tier NOT IN ('free','') GROUP BY tier"
    ) as cur:
        async for r in cur:
            by_tier[r["tier"]] = r["n"]
    gross = sum((TIER_PRICE_USD.get(t, 0) * n) for t, n in by_tier.items())
    ev = await (await db.execute("SELECT COUNT(*) AS n FROM subscription_events")).fetchone()
    return {
        "total_users": (row["users"] if row else 0) or 0,
        "ever_subscribed": (row["ever"] if row else 0) or 0,
        "paid_now": (row["paid_now"] if row else 0) or 0,
        "active_by_tier": by_tier,
        "current_mrr_gross_usd": round(gross, 2),
        "current_mrr_net_usd": round(gross * APPLE_NET_FACTOR, 2),
        "total_events": (ev["n"] if ev else 0) or 0,
    }


async def monthly_aggregates(db: aiosqlite.Connection) -> list[dict]:
    """Replay the event log into a month-by-month report.

    For each calendar month (UTC) from the first event to now: active paid
    subscribers by tier at month end, new subscriptions and churns within the
    month, and gross/net MRR from the end-of-month active set. State is carried
    forward across months, so a subscriber with no event in a month stays
    counted until they churn.
    """
    rows = await (await db.execute(
        "SELECT user_id, event_type, to_tier, effective_at "
        "FROM subscription_events ORDER BY effective_at ASC, recorded_at ASC"
    )).fetchall()
    if not rows:
        return []

    def month_of(iso: str) -> str:
        return (iso or "")[:7]  # YYYY-MM

    state: dict[str, str | None] = {}  # user_id -> current paid tier or None
    first_month = month_of(rows[0]["effective_at"])
    last_month = month_of(_now_iso())

    # Bucket events by month for ordered replay.
    by_month: dict[str, list] = {}
    for r in rows:
        by_month.setdefault(month_of(r["effective_at"]), []).append(r)

    # Iterate inclusive month range first_month..last_month.
    def months_range(start: str, end: str) -> list[str]:
        sy, sm = int(start[:4]), int(start[5:7])
        ey, em = int(end[:4]), int(end[5:7])
        out = []
        y, m = sy, sm
        while (y, m) <= (ey, em):
            out.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    report = []
    for month in months_range(first_month, last_month):
        new_subs = churns = 0
        for r in by_month.get(month, []):
            uid = r["user_id"]
            was_paid = is_paid_tier(state.get(uid))
            to_tier = r["to_tier"] if is_paid_tier(r["to_tier"]) else None
            now_paid = to_tier is not None
            state[uid] = to_tier
            if now_paid and not was_paid:
                new_subs += 1
            elif was_paid and not now_paid:
                churns += 1
        active_by_tier: dict[str, int] = {}
        for t in state.values():
            if is_paid_tier(t):
                active_by_tier[t] = active_by_tier.get(t, 0) + 1
        gross = sum(TIER_PRICE_USD.get(t, 0) * n for t, n in active_by_tier.items())
        report.append({
            "month": month,
            "active_by_tier": active_by_tier,
            "active_total": sum(active_by_tier.values()),
            "new_subscriptions": new_subs,
            "churns": churns,
            "gross_usd": round(gross, 2),
            "net_usd": round(gross * APPLE_NET_FACTOR, 2),
        })
    return report


async def user_timeline(db: aiosqlite.Connection, user_id: str) -> list[dict]:
    """The raw event timeline for one user (oldest first)."""
    rows = await (await db.execute(
        "SELECT event_type, notification_type, subtype, from_tier, to_tier, "
        "product_id, expires_at, environment, source, price_usd, effective_at, "
        "recorded_at, offer_id FROM subscription_events WHERE user_id = ? "
        "ORDER BY effective_at ASC, recorded_at ASC",
        (user_id,),
    )).fetchall()
    return [dict(r) for r in rows]


async def recent_events(db: aiosqlite.Connection, limit: int = 200) -> list[dict]:
    """Newest events across all users, enriched with the user's email/tier."""
    limit = max(1, min(limit, 1000))
    rows = await (await db.execute(
        "SELECT e.user_id, e.event_type, e.from_tier, e.to_tier, e.product_id, "
        "e.environment, e.source, e.price_usd, e.effective_at, e.offer_id, "
        "u.email, u.tier AS current_tier "
        "FROM subscription_events e LEFT JOIN users u ON u.id = e.user_id "
        "ORDER BY e.effective_at DESC, e.recorded_at DESC LIMIT ?",
        (limit,),
    )).fetchall()
    return [dict(r) for r in rows]


async def redemptions_by_offer(
    db: aiosqlite.Connection, offer_id: str | None = None, limit: int = 500
) -> dict:
    """Redemption attribution for ASC offer pools (SS email-code campaigns,
    2026-07-17). offer_id here is the ASC offer reference the client reads
    from StoreKit's transaction.offer.id — Apple never exposes the redeemed
    code string, so this is the finest grain available; SS joins it against
    their send log for per-user, per-code attribution.

    Returns {"offers": [...per-offer counts...], "redemptions": [...rows...]};
    pass offer_id to narrow the row list to one pool."""
    limit = max(1, min(limit, 1000))
    counts = await (await db.execute(
        "SELECT offer_id, COUNT(*) AS redemptions, COUNT(DISTINCT user_id) AS users, "
        "MIN(effective_at) AS first_at, MAX(effective_at) AS last_at "
        "FROM subscription_events WHERE offer_id IS NOT NULL "
        "GROUP BY offer_id ORDER BY last_at DESC"
    )).fetchall()
    where, params = "e.offer_id IS NOT NULL", []
    if offer_id:
        where, params = "e.offer_id = ?", [offer_id]
    rows = await (await db.execute(
        "SELECT e.offer_id, e.user_id, e.event_type, e.subtype, e.from_tier, "
        "e.to_tier, e.product_id, e.environment, e.source, e.effective_at, "
        "u.email, u.tier AS current_tier "
        f"FROM subscription_events e LEFT JOIN users u ON u.id = e.user_id "
        f"WHERE {where} ORDER BY e.effective_at DESC, e.recorded_at DESC LIMIT ?",
        (*params, limit),
    )).fetchall()
    return {"offers": [dict(r) for r in counts], "redemptions": [dict(r) for r in rows]}
