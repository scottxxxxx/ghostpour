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
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.subscriptions")

# List price per paid tier (what the customer pays Apple), for the bookkeeping
# report. This is the subscription price, NOT the tiers.yml budget cap. Apple
# remits proceeds after its commission; NET_FACTOR is the small-business 15%.
TIER_PRICE_USD: dict[str, float] = {"plus": 9.99, "pro": 14.99}
APPLE_NET_FACTOR = 0.85  # proceeds after Apple's 15% commission

# Normalized event types we record. Raw Apple notificationType is kept alongside.
PAID_EVENT_TYPES = {"subscribed", "renewed", "upgraded"}
# Event types that establish "this user has a SUBSCRIPTION RECORD at some
# point". ⚠ NOT "has paid": a FREE TRIAL START is a `subscribed` event, so a
# user who trialled for seven days and lapsed without ever being billed sets
# `ever_subscribed` exactly like a payer does. The old wording here claimed
# "has paid at some point", which is false for every trial, and the
# acquisition report believed it (2026-09-08) — one keyword-level subscriber
# count that could not tell a lapsed trial from a customer.
#
# The behaviour is UNCHANGED and deliberately so: `ever_subscribed` is a
# sticky cache of "began a subscription", which is the right funnel step and
# the right denominator for trial→paid. Only the claim about it was wrong.
# For "money actually moved", read a `renewed`/`upgraded` row out of
# subscription_events; see the acquisition report's `paid` column.
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
    price_paid: float | None = None,
    currency: str | None = None,
    offer_type: int | None = None,
    offer_discount_type: str | None = None,
    auto_renew_status: int | None = None,
) -> str:
    """Append one subscription event and keep the user-row caches in lockstep.

    `price_usd` is LIST-price bookkeeping and says nothing about money. The
    five trailing fields are what Apple actually said (see
    `money_fields_from_apple`); `price_paid` is the only field that means a
    charge happened, and only when it is greater than zero.

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
             recorded_at, raw, offer_id,
             price_paid, currency, offer_type, offer_discount_type, auto_renew_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, user_id, event_type, notification_type, subtype, from_tier,
            to_tier, product_id, original_transaction_id, transaction_id,
            expires_at, environment, source, price_usd, eff,
            _now_iso(), json.dumps(raw) if raw is not None else None, offer_id,
            price_paid, currency, offer_type, offer_discount_type, auto_renew_status,
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
# What Apple actually said (2026-09-10)
# ---------------------------------------------------------------------------

def money_fields_from_apple(transaction_info: dict | None,
                            renewal_info: dict | None = None) -> dict:
    """The five truth fields, read off Apple's decoded transaction and
    renewal payloads. Apple's `price` is in MILLIUNITS of `currency`
    (9990 = 9.99), so it is divided here and nowhere else. Missing fields
    stay None rather than defaulting: a None price is "Apple did not say",
    which must never read as "free" or as "paid"."""
    t = transaction_info or {}
    r = renewal_info or {}
    price = t.get("price")
    try:
        price_paid = round(int(price) / 1000.0, 2) if price is not None else None
    except (TypeError, ValueError):
        price_paid = None
    ars = r.get("autoRenewStatus")
    try:
        auto_renew = int(ars) if ars is not None else None
    except (TypeError, ValueError):
        auto_renew = None
    ot = t.get("offerType")
    try:
        offer_type = int(ot) if ot is not None else None
    except (TypeError, ValueError):
        offer_type = None
    return {
        "price_paid": price_paid,
        "currency": t.get("currency"),
        "offer_type": offer_type,
        "offer_discount_type": t.get("offerDiscountType"),
        "auto_renew_status": auto_renew,
    }


async def upsert_subscription_status(
    db: aiosqlite.Connection, *, original_transaction_id: str, source: str,
    user_id: str | None = None, product_id: str | None = None, tier: str | None = None,
    environment: str | None = None, status: int | None = None,
    auto_renew_status: int | None = None, offer_type: int | None = None,
    offer_discount_type: str | None = None, price_paid: float | None = None,
    currency: str | None = None, expires_at: str | None = None,
    transaction_id: str | None = None, commit: bool = True,
) -> None:
    """One row per subscription. A None argument keeps the stored value
    (COALESCE), so a notification that carries no renewal info does not
    erase an auto_renew_status a previous one set. `paid_ever` only ever
    turns on."""
    paid_now = 1 if (price_paid or 0) > 0 else 0
    await db.execute(
        """INSERT INTO subscription_status
             (original_transaction_id, user_id, product_id, tier, environment, status,
              auto_renew_status, offer_type, offer_discount_type, price_paid, currency,
              expires_at, paid_ever, transaction_id, checked_at, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(original_transaction_id) DO UPDATE SET
             user_id=COALESCE(excluded.user_id, user_id),
             product_id=COALESCE(excluded.product_id, product_id),
             tier=COALESCE(excluded.tier, tier),
             environment=COALESCE(excluded.environment, environment),
             status=COALESCE(excluded.status, status),
             auto_renew_status=COALESCE(excluded.auto_renew_status, auto_renew_status),
             offer_type=COALESCE(excluded.offer_type, offer_type),
             offer_discount_type=COALESCE(excluded.offer_discount_type, offer_discount_type),
             price_paid=COALESCE(excluded.price_paid, price_paid),
             currency=COALESCE(excluded.currency, currency),
             expires_at=COALESCE(excluded.expires_at, expires_at),
             paid_ever=MAX(paid_ever, excluded.paid_ever),
             transaction_id=COALESCE(excluded.transaction_id, transaction_id),
             checked_at=excluded.checked_at,
             source=excluded.source""",
        (original_transaction_id, user_id, product_id, tier, environment, status,
         auto_renew_status, offer_type, offer_discount_type, price_paid, currency,
         expires_at, paid_now, transaction_id, _now_iso(), source),
    )
    if commit:
        await db.commit()


async def refresh_status_from_apple(db: aiosqlite.Connection) -> dict:
    """Pull Apple's subscription status for every subscription GP knows and
    (a) upsert `subscription_status`, (b) backfill the money fields on the
    events that were recorded before GP captured them, matched by
    transaction_id. Apple is the only witness to whether money moved, so
    this is the reconciliation the dashboard's headline rests on.

    Returns counts. Fail-soft per subscription: one 404 or timeout must not
    stop the sweep, and a subscription Apple has no record of is reported
    rather than guessed."""
    from app.services import app_store_server_api as assa
    if not assa.is_configured():
        return {"checked": 0, "updated": 0, "backfilled_events": 0, "skipped": "not_configured"}
    rows = await (await db.execute(
        """SELECT DISTINCT original_transaction_id AS otid FROM (
             SELECT original_transaction_id FROM subscription_events
              WHERE original_transaction_id IS NOT NULL AND original_transaction_id NOT IN ('', '0')
             UNION
             SELECT original_transaction_id FROM users
              WHERE original_transaction_id IS NOT NULL AND original_transaction_id NOT IN ('', '0'))"""
    )).fetchall()
    checked = updated = backfilled = 0
    missing: list[str] = []
    for r in rows:
        otid = r["otid"]
        checked += 1
        try:
            state = await assa.get_subscription_state(otid)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_status: %s failed: %s", otid, e)
            continue
        if state is None:
            missing.append(otid)
            continue
        urow = await (await db.execute(
            "SELECT user_id FROM subscription_events WHERE original_transaction_id=? "
            "ORDER BY effective_at DESC LIMIT 1", (otid,))).fetchone()
        user_id = urow["user_id"] if urow else None
        if user_id is None:
            u2 = await (await db.execute(
                "SELECT id FROM users WHERE original_transaction_id=?", (otid,))).fetchone()
            user_id = u2["id"] if u2 else None
        await upsert_subscription_status(
            db, original_transaction_id=otid, source="apple_status", user_id=user_id,
            product_id=state.get("product_id"), tier=state.get("tier"),
            environment=state.get("environment"), status=state.get("status"),
            auto_renew_status=state.get("auto_renew_status"),
            offer_type=state.get("offer_type"),
            offer_discount_type=state.get("offer_discount_type"),
            price_paid=state.get("price_paid"), currency=state.get("currency"),
            expires_at=state.get("expires_at"), transaction_id=state.get("transaction_id"),
            commit=False,
        )
        updated += 1
        # Backfill the events Apple can vouch for, by transaction id, only
        # where GP recorded nothing. Never overwrite a value ASSN wrote.
        for txn in state.get("transactions") or []:
            tid = txn.get("transaction_id")
            if not tid:
                continue
            cur = await db.execute(
                """UPDATE subscription_events
                      SET price_paid=COALESCE(price_paid, ?), currency=COALESCE(currency, ?),
                          offer_type=COALESCE(offer_type, ?),
                          offer_discount_type=COALESCE(offer_discount_type, ?)
                    WHERE transaction_id=? AND price_paid IS NULL""",
                (txn.get("price_paid"), txn.get("currency"), txn.get("offer_type"),
                 txn.get("offer_discount_type"), tid),
            )
            backfilled += cur.rowcount or 0
            if (txn.get("price_paid") or 0) > 0:
                await db.execute(
                    "UPDATE subscription_status SET paid_ever=1 WHERE original_transaction_id=?",
                    (otid,))
    await db.commit()
    logger.info("refresh_status checked=%d updated=%d backfilled_events=%d missing=%d",
                checked, updated, backfilled, len(missing))
    return {"checked": checked, "updated": updated, "backfilled_events": backfilled,
            "missing_at_apple": missing}


def classify_status(row: dict) -> str:
    """One word per subscription, for the dashboard. Reads only what Apple
    said. `paying` needs a non-zero charge on the CURRENT period; a trial
    or an offer code at price 0 is never paying, whatever the tier."""
    status = row.get("status")
    active = status in (1, 4)          # active, or grace period
    ars = row.get("auto_renew_status")
    price = row.get("price_paid") or 0
    disc = row.get("offer_discount_type")
    otype = row.get("offer_type")
    if not active:
        if disc == "FREE_TRIAL" or (otype == 1 and price == 0):
            return "trial_lapsed"
        if otype == 3 and price == 0:
            return "offer_lapsed"
        return "lapsed"
    if price > 0:
        return "paying" if ars != 0 else "paying_cancelled"
    if disc == "FREE_TRIAL" or otype == 1:
        return "trialing" if ars != 0 else "trial_cancelled"
    if otype in (2, 3, 4):
        return "on_offer" if ars != 0 else "offer_cancelled"
    return "active_unpriced"


async def subscription_truth(db: aiosqlite.Connection) -> dict:
    """The headline the Subscriptions tab should quote, and the per-subscriber
    rows behind it. Production only; money recognised only where Apple
    reported a non-zero charge (Scott, 2026-09-10: "we should not recognize
    MRR since we have never received money")."""
    rows = [dict(r) for r in await (await db.execute(
        """SELECT s.*, u.email FROM subscription_status s
           LEFT JOIN users u ON u.id = s.user_id
          ORDER BY s.expires_at DESC""")).fetchall()]
    counts: dict[str, int] = {}
    mrr = 0.0
    subscribers = []
    for r in rows:
        prod = (r.get("environment") or "") == PRODUCTION
        state = classify_status(r)
        if prod:
            counts[state] = counts.get(state, 0) + 1
            if state in ("paying", "paying_cancelled"):
                mrr += TIER_PRICE_USD.get(r.get("tier") or "", 0)
        subscribers.append({
            "email": r.get("email"), "user_id": r.get("user_id"), "tier": r.get("tier"),
            "environment": r.get("environment"), "state": state,
            "auto_renew": r.get("auto_renew_status"), "offer_type": r.get("offer_type"),
            "offer_discount_type": r.get("offer_discount_type"),
            "price_paid": r.get("price_paid"), "currency": r.get("currency"),
            "expires_at": r.get("expires_at"), "paid_ever": bool(r.get("paid_ever")),
            "checked_at": r.get("checked_at"), "source": r.get("source"),
            "original_transaction_id": r.get("original_transaction_id"),
        })
    received: dict[str, float] = {}
    async with db.execute(
        """SELECT currency, SUM(price_paid) AS total FROM subscription_events
            WHERE environment=? AND price_paid > 0 GROUP BY currency""", (PRODUCTION,)) as cur:
        async for r in cur:
            received[r["currency"] or "?"] = round(r["total"] or 0.0, 2)
    last = await (await db.execute("SELECT MAX(checked_at) AS t FROM subscription_status")).fetchone()
    return {
        "paying_now": counts.get("paying", 0) + counts.get("paying_cancelled", 0),
        "mrr_recognized_usd": round(mrr, 2),
        "mrr_recognized_net_usd": round(mrr * APPLE_NET_FACTOR, 2),
        "trialing_auto_renew_on": counts.get("trialing", 0),
        "trialing_cancelled": counts.get("trial_cancelled", 0),
        "on_offer": counts.get("on_offer", 0) + counts.get("offer_cancelled", 0),
        "trials_lapsed": counts.get("trial_lapsed", 0),
        "lapsed_other": counts.get("lapsed", 0) + counts.get("offer_lapsed", 0),
        "received_to_date": received,
        "by_state": counts,
        "subscribers": subscribers,
        "last_checked_at": last["t"] if last else None,
    }


# ---------------------------------------------------------------------------
# Reporting (admin dashboard)
# ---------------------------------------------------------------------------

PRODUCTION = "Production"
SANDBOX = "Sandbox"


async def user_environments(db: aiosqlite.Connection) -> dict[str, str]:
    """Map user_id -> StoreKit environment, from their latest event that
    recorded one.

    Sandbox (TestFlight) subscriptions are indistinguishable from real ones
    in every field except this, and they renew and lapse on their own fast
    cadence, so counting them as revenue overstated MRR by more than it
    reported (2026-08-02: three of five paid accounts were TestFlight).

    Users absent from the map have no environment on any event: either they
    were granted a tier with no purchase behind it, or their only events
    predate environment capture on the verify-receipt path. They are counted
    separately rather than folded into either side, because guessing would
    put test accounts in the revenue number, which is the failure this
    function exists to prevent.
    """
    rows = await (await db.execute(
        "SELECT user_id, environment FROM subscription_events "
        "WHERE environment IS NOT NULL "
        "ORDER BY recorded_at ASC, effective_at ASC"
    )).fetchall()
    # Ascending order means the last write per user wins.
    return {r["user_id"]: r["environment"] for r in rows}


def _env_bucket(env: str | None) -> str:
    if env == PRODUCTION:
        return "production"
    if env == SANDBOX:
        return "sandbox"
    return "unknown"


async def summary(db: aiosqlite.Connection) -> dict:
    """Top-line counts for the Subscriptions tab header.

    The headline money figures (`current_mrr_gross_usd` / `_net_usd`) count
    Production accounts ONLY. Sandbox and unclassified totals ride alongside
    so nothing is hidden, but the number that gets quoted is real revenue.
    """
    row = await (await db.execute(
        "SELECT COUNT(*) AS users, "
        "SUM(CASE WHEN ever_subscribed = 1 THEN 1 ELSE 0 END) AS ever, "
        "SUM(CASE WHEN tier NOT IN ('free','') THEN 1 ELSE 0 END) AS paid_now "
        "FROM users"
    )).fetchone()
    envs = await user_environments(db)

    by_tier: dict[str, int] = {}
    by_tier_production: dict[str, int] = {}
    paid_by_env = {"production": 0, "sandbox": 0, "unknown": 0}
    gross_by_env = {"production": 0.0, "sandbox": 0.0, "unknown": 0.0}
    async with db.execute(
        "SELECT id, tier FROM users WHERE tier NOT IN ('free','')"
    ) as cur:
        async for r in cur:
            tier = r["tier"]
            bucket = _env_bucket(envs.get(r["id"]))
            by_tier[tier] = by_tier.get(tier, 0) + 1
            paid_by_env[bucket] += 1
            gross_by_env[bucket] += TIER_PRICE_USD.get(tier, 0)
            if bucket == "production":
                by_tier_production[tier] = by_tier_production.get(tier, 0) + 1

    gross = gross_by_env["production"]
    ev = await (await db.execute("SELECT COUNT(*) AS n FROM subscription_events")).fetchone()
    return {
        "total_users": (row["users"] if row else 0) or 0,
        "ever_subscribed": (row["ever"] if row else 0) or 0,
        "paid_now": paid_by_env["production"],
        "paid_now_all_envs": (row["paid_now"] if row else 0) or 0,
        "paid_by_env": paid_by_env,
        "active_by_tier": by_tier_production,
        "active_by_tier_all_envs": by_tier,
        "current_mrr_gross_usd": round(gross, 2),
        "current_mrr_net_usd": round(gross * APPLE_NET_FACTOR, 2),
        "sandbox_mrr_gross_usd": round(gross_by_env["sandbox"], 2),
        "unknown_mrr_gross_usd": round(gross_by_env["unknown"], 2),
        "total_events": (ev["n"] if ev else 0) or 0,
    }


async def monthly_aggregates(db: aiosqlite.Connection) -> list[dict]:
    """Replay the event log into a month-by-month report.

    For each calendar month (UTC) from the first event to now: active paid
    subscribers by tier at month end, new subscriptions and churns within the
    month, and gross/net MRR from the end-of-month active set. State is carried
    forward across months, so a subscriber with no event in a month stays
    counted until they churn.

    Production accounts only: Sandbox (TestFlight) renewal cycles would
    otherwise show up as subscriptions and churn that never happened.
    """
    envs = await user_environments(db)
    rows = [r for r in await (await db.execute(
        "SELECT user_id, event_type, to_tier, effective_at "
        "FROM subscription_events ORDER BY effective_at ASC, recorded_at ASC"
    )).fetchall() if envs.get(r["user_id"]) == PRODUCTION]
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


async def mrr_trend(db: aiosqlite.Connection) -> list[dict]:
    """Daily MRR run-rate replayed from the event log (Scott 2026-07-28).

    Same state semantics as monthly_aggregates (an event's to_tier sets
    the user's paid state; anything non-paid clears it), sampled at
    end-of-day instead of end-of-month so the launch-era curve is
    visible day by day. List price by convention (matches the tab: MRR
    is list price, net is after Apple's 15%); trials and offer periods
    count at list value until they lapse.

    The headline series is Production only. Sandbox rides alongside as its
    own series rather than being dropped, so a TestFlight cohort is still
    visible on the tab, just never mixed into the money line.
    """
    envs = await user_environments(db)
    rows = await (await db.execute(
        "SELECT user_id, event_type, to_tier, effective_at "
        "FROM subscription_events ORDER BY effective_at ASC, recorded_at ASC"
    )).fetchall()
    if not rows:
        return []

    def day_of(iso: str) -> str:
        return (iso or "")[:10]

    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(day_of(r["effective_at"]), []).append(r)

    first = datetime.fromisoformat(day_of(rows[0]["effective_at"]))
    last = datetime.now(timezone.utc).replace(tzinfo=None)
    state: dict[str, str | None] = {}
    trend = []
    d = first
    while day_of(d.isoformat()) <= day_of(last.isoformat()):
        day = day_of(d.isoformat())
        for r in by_day.get(day, []):
            to_tier = r["to_tier"] if is_paid_tier(r["to_tier"]) else None
            state[r["user_id"]] = to_tier
        gross = sum(TIER_PRICE_USD.get(t, 0)
                    for uid, t in state.items()
                    if is_paid_tier(t) and envs.get(uid) == PRODUCTION)
        gross_sandbox = sum(TIER_PRICE_USD.get(t, 0)
                            for uid, t in state.items()
                            if is_paid_tier(t) and envs.get(uid) == SANDBOX)
        trend.append({
            "day": day,
            "mrr_gross_sandbox_usd": round(gross_sandbox, 2),
            "active_paid_sandbox": sum(
                1 for uid, t in state.items()
                if is_paid_tier(t) and envs.get(uid) == SANDBOX),
            "mrr_gross_usd": round(gross, 2),
            "mrr_net_usd": round(gross * APPLE_NET_FACTOR, 2),
            "active_paid": sum(
                1 for uid, t in state.items()
                if is_paid_tier(t) and envs.get(uid) == PRODUCTION),
        })
        d += timedelta(days=1)
    return trend


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
