"""Project Chat free-tier quota helper.

Lazy reset on every read/write — no cron job. The counter and period live
on `users` and are checked against the current calendar month (UTC) on
every operation. If the stored period is stale, the effective counter is
zero and a fresh period is materialized on the next decrement.

See docs/wire-contracts/project-chat.md for the full spec.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.models.user import UserRecord


def current_period_utc(now: datetime | None = None) -> str:
    """Return the current calendar-month period key in UTC, e.g. '2026-04'."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def next_period_resets_at(now: datetime | None = None) -> str:
    """ISO timestamp of the upcoming month boundary (UTC midnight on the 1st)."""
    now = now or datetime.now(timezone.utc)
    # Last day of current month, then +1 day rolls to the 1st of next month.
    last_day = calendar.monthrange(now.year, now.month)[1]
    end_of_month = now.replace(
        day=last_day, hour=23, minute=59, second=59, microsecond=999999
    )
    next_first = (end_of_month + timedelta(microseconds=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_first.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class QuotaState:
    used: int                # virtual count for the current period (0 if stale)
    total: int               # configured cap (-1 = unlimited; 0 = no free uses)
    remaining: int | None    # max(total - used, 0); None when total == -1
    has_quota: bool          # True when any free uses remain (or unlimited)
    resets_at: str           # ISO timestamp of next reset (always set)


def read_quota_state(
    user: UserRecord,
    free_quota_per_month: int,
    *,
    now: datetime | None = None,
) -> QuotaState:
    """Compute the user's current Project Chat quota state.

    Pure read — does not write to the DB. The "virtual reset" happens here:
    if the stored period doesn't match the current calendar month, treat
    used as 0.
    """
    period = current_period_utc(now)
    stored_period = user.project_chat_period
    used = (
        user.project_chat_used_this_period
        if stored_period == period
        else 0
    )

    if free_quota_per_month == -1:
        return QuotaState(
            used=used,
            total=-1,
            remaining=None,
            has_quota=True,
            resets_at=next_period_resets_at(now),
        )

    remaining = max(free_quota_per_month - used, 0)
    return QuotaState(
        used=used,
        total=free_quota_per_month,
        remaining=remaining,
        has_quota=remaining > 0,
        resets_at=next_period_resets_at(now),
    )


async def decrement_quota(
    db: aiosqlite.Connection,
    user_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Atomically increment used count, materializing a fresh period if needed.

    Should be called only when GP is processing a `send_to_gp_with_cta`
    outcome for a Free user. Caller is responsible for committing the
    transaction.
    """
    period = current_period_utc(now)
    # If the stored period matches, increment in place. Otherwise reset
    # to 1 and stamp the new period. Done in a single UPDATE to avoid the
    # read-then-write race.
    await db.execute(
        """UPDATE users SET
            project_chat_period = ?,
            project_chat_used_this_period = CASE
                WHEN project_chat_period = ? THEN project_chat_used_this_period + 1
                ELSE 1
            END,
            updated_at = ?
           WHERE id = ?""",
        (period, period, datetime.now(timezone.utc).isoformat(), user_id),
    )


async def zero_quota_on_tier_change(
    db: aiosqlite.Connection,
    user_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Zero the counter on Free → Plus/Pro upgrade.

    Called from /v1/verify-receipt when a real state change happens. Sets
    period to current so the user starts the new tier with a clean counter
    that won't ghost-decrement on the first virtual-reset read.
    """
    period = current_period_utc(now)
    await db.execute(
        """UPDATE users SET
            project_chat_used_this_period = 0,
            project_chat_period = ?
           WHERE id = ?""",
        (period, user_id),
    )
