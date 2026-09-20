"""Centralized helpers for monthly allocation resets.

Three problems this module solves:

1. Drift: `now + timedelta(days=30)` accumulates ~5 days of error per year
   relative to Apple's calendar-month billing. Use `relativedelta(months=1)`.

2. Apple alignment: when Apple notifies us of a renewal, the transaction
   carries `expiresDate` — the authoritative next-renewal timestamp. Prefer
   that over locally-computed dates so our cycle stays in sync with what
   Apple actually charges.

3. Stale resets: a user inactive past their `allocation_resets_at` should
   still get a fresh allocation on next access. Lazy-reset on read handles
   missed/delayed Apple webhooks AND Free users who have no webhook path.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
from dateutil.relativedelta import relativedelta


def compute_next_reset(
    now: datetime,
    apple_expires_date_ms: int | None = None,
) -> datetime:
    """Compute the next `allocation_resets_at` value.

    When Apple's `expiresDate` is available (DID_RENEW / SUBSCRIBED), use
    that — it has all the calendar/end-of-month edge cases baked in and
    stays in sync with Apple's actual billing. Otherwise fall back to
    `now + 1 calendar month` (Free tier, admin tier changes, trial-to-paid
    conversions where we don't have a transaction handy).
    """
    if apple_expires_date_ms is not None:
        return datetime.fromtimestamp(apple_expires_date_ms / 1000, tz=timezone.utc)
    return now + relativedelta(months=1)


def roll_forward_past(
    allocation_resets_at: datetime,
    now: datetime,
) -> datetime:
    """Roll a stale `allocation_resets_at` forward in 1-month increments
    until it's strictly after `now`.

    Preserves the user's day-of-month anchor across multiple missed cycles
    by always computing from the original stale date (via
    `relativedelta(months=N)`) rather than chaining single-month deltas.
    Chaining loses the anchor after the first end-of-month snap-back —
    e.g., Jan 31 + 1mo = Feb 28, but Feb 28 + 1mo = Mar 28 (NOT Mar 31).
    Apple snaps back to the original day when the target month has it,
    and so do we.
    """
    n = 1
    while True:
        candidate = allocation_resets_at + relativedelta(months=n)
        if candidate > now:
            return candidate
        n += 1


def period_start(allocation_resets_at: datetime | None, now: datetime) -> datetime:
    """When the user's CURRENT allocation period began.

    Periods are one calendar month ending at `allocation_resets_at`. If that
    reset is stale (in the past, the lazy reset has not run yet), the current
    period began AT the missed reset. If it is unset, fall back to the start
    of the calendar month, which is what "monthly" means with no anchor.
    """
    if allocation_resets_at is None:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if allocation_resets_at <= now:
        return allocation_resets_at
    start = allocation_resets_at - relativedelta(months=1)
    # An anchor more than a month out (prod had one on 2026-09-20; the test
    # fixture uses 2099) would put the start in the future and count NOTHING,
    # silently blinding the alert for that user. A monthly allowance is at
    # most a month wide, so fall back to the trailing month.
    if start > now:
        return now - relativedelta(months=1)
    return start


async def real_spend_alerts(db, now: datetime, threshold: float = 0.8) -> list[dict]:
    """Users whose REAL spend this period is at or above `threshold` of their
    cap, from usage_log, not from the meter.

    ⚠ WHY NOT THE METER. Downgrade-to-free deliberately SETS monthly_used_usd
    to the free cap (apple_webhooks._downgrade_to_free and /sync-subscription)
    so a lapsed trial cannot double dip on a fresh free allowance. An alert
    that reads the meter therefore fires on every lapsed trial, showing a $2
    overage for a user whose real spend was $0.17 (d197d592, 2026-09-20). The
    meter is right for GATING and wrong for ALERTING, because it cannot tell
    "spent to the cap" from "set to the cap". This reads what was actually
    spent, over the user's own period, and reports both numbers so the page
    can show the gap rather than hide it.
    """
    cursor = await db.execute(
        """SELECT id, email, tier, monthly_used_usd, monthly_cost_limit_usd,
                  allocation_resets_at
           FROM users
           WHERE is_active = 1 AND monthly_cost_limit_usd > 0"""
    )
    users = await cursor.fetchall()
    out: list[dict] = []
    for u in users:
        limit = float(u["monthly_cost_limit_usd"] or 0)
        start = period_start(parse_iso(u["allocation_resets_at"]), now)
        # request_timestamp and this ISO string share the same shape
        # (YYYY-MM-DDTHH:MM:SS...+00:00), so the string compare is a time
        # compare. A datetime() on one side and not the other is the trap.
        cursor = await db.execute(
            """SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log
               WHERE user_id = ? AND request_timestamp >= ?""",
            (u["id"], start.isoformat()),
        )
        spend = float((await cursor.fetchone())[0] or 0)
        if spend >= limit * threshold:
            out.append({
                "user_id": u["id"],
                "email": u["email"],
                "tier": u["tier"],
                # The alert's number IS the real spend now. Kept under the
                # key the dashboard already reads, with the meter beside it.
                "monthly_used_usd": round(spend, 4),
                "meter_usd": round(float(u["monthly_used_usd"] or 0), 4),
                "monthly_limit_usd": round(limit, 4),
                "percent_used": round(spend / limit * 100, 1),
                "period_start": start.isoformat(),
            })
    out.sort(key=lambda a: a["percent_used"], reverse=True)
    return out


def parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into an aware UTC datetime.
    Returns None if input is None or unparseable."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def lazy_reset_if_due(
    db: aiosqlite.Connection,
    user_id: str,
    now: datetime | None = None,
) -> bool:
    """If the user's `allocation_resets_at` is in the past, reset their
    monthly counters and roll the reset date forward.

    Atomic via a WHERE-guarded UPDATE: if two requests race the same user,
    only one wins; the loser's UPDATE matches zero rows and exits cleanly.

    Returns True if a reset was applied, False if not yet due (or no row).

    Resets:
      - monthly_used_usd → 0
      - overage_balance_usd → 0
      - searches_used → 0
      - generations_used → 0
      - allocation_resets_at → rolled forward past `now`
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cursor = await db.execute(
        "SELECT allocation_resets_at FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False

    current = parse_iso(row["allocation_resets_at"] if hasattr(row, "keys") else row[0])
    if current is None or current > now:
        return False

    next_reset = roll_forward_past(current, now)

    # WHERE clause guards against double-reset if two requests race.
    cursor = await db.execute(
        """UPDATE users
              SET monthly_used_usd = 0,
                  overage_balance_usd = 0,
                  searches_used = 0,
                  generations_used = 0,
                  allocation_resets_at = ?,
                  updated_at = ?
            WHERE id = ?
              AND allocation_resets_at = ?""",
        (
            next_reset.isoformat(),
            now.isoformat(),
            user_id,
            row["allocation_resets_at"] if hasattr(row, "keys") else row[0],
        ),
    )
    await db.commit()
    return cursor.rowcount > 0
