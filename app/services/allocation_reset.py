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
      - searches_used   → 0   (no-op until that column lands; safe to set)
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
