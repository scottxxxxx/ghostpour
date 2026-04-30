"""Memory-capture free-tier quota helper.

End-of-meeting CQ captures (`/v1/capture-transcript`) are metered for Free
users. Mirrors `project_chat_quota` exactly: lazy reset on every read/write
keyed by calendar-month UTC, no cron job. The counter and period live on
`users.memory_used_this_period` + `users.memory_period`.

See docs/wire-contracts/memory-capture.md for the full spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from app.models.user import UserRecord
from app.services.project_chat_quota import (
    current_period_utc,
    next_period_resets_at,
)


@dataclass(frozen=True)
class MemoryQuotaState:
    used: int                # virtual count for the current period (0 if stale)
    total: int               # configured cap (-1 = unlimited; 0 = no free captures)
    remaining: int | None    # max(total - used, 0); None when total == -1
    has_quota: bool          # True when any free captures remain (or unlimited)
    resets_at: str           # ISO timestamp of next reset


def read_memory_quota_state(
    user: UserRecord,
    free_quota_per_month: int,
    *,
    now: datetime | None = None,
) -> MemoryQuotaState:
    """Compute the user's current memory-capture quota state.

    Pure read — does not write to the DB. The "virtual reset" happens here:
    if the stored period doesn't match the current calendar month, treat
    used as 0.
    """
    period = current_period_utc(now)
    used = (
        user.memory_used_this_period
        if user.memory_period == period
        else 0
    )

    if free_quota_per_month == -1:
        return MemoryQuotaState(
            used=used,
            total=-1,
            remaining=None,
            has_quota=True,
            resets_at=next_period_resets_at(now),
        )

    remaining = max(free_quota_per_month - used, 0)
    return MemoryQuotaState(
        used=used,
        total=free_quota_per_month,
        remaining=remaining,
        has_quota=remaining > 0,
        resets_at=next_period_resets_at(now),
    )


async def decrement_memory_quota(
    db: aiosqlite.Connection,
    user_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Atomically increment used count, materializing a fresh period if needed.

    Caller is responsible for committing the transaction. Should be called
    only when GP is processing a `capture_with_cta` outcome for a Free user.
    """
    period = current_period_utc(now)
    await db.execute(
        """UPDATE users SET
            memory_period = ?,
            memory_used_this_period = CASE
                WHEN memory_period = ? THEN memory_used_this_period + 1
                ELSE 1
            END
           WHERE id = ?""",
        (period, period, user_id),
    )


async def zero_memory_quota_on_tier_change(
    db: aiosqlite.Connection,
    user_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Zero the memory counter on Free → Plus/Pro upgrade.

    Called from /v1/verify-receipt on real state changes so the new
    subscriber starts the period with a clean counter that won't
    ghost-decrement on the first virtual-reset read.
    """
    period = current_period_utc(now)
    await db.execute(
        """UPDATE users SET
            memory_used_this_period = 0,
            memory_period = ?
           WHERE id = ?""",
        (period, user_id),
    )


async def stamp_meeting_cta(
    db: aiosqlite.Connection,
    user_id: str,
    origin_id: str,
    cta_kind: str,
) -> None:
    """Record the meeting + CTA kind that the next quilt fetch should surface.

    Single-shot per meeting: cleared by the quilt-fetch interceptor after
    one render so the upsell card doesn't re-appear on every refresh.
    """
    await db.execute(
        """UPDATE users SET
            memory_last_origin_id = ?,
            memory_last_cta_kind = ?
           WHERE id = ?""",
        (origin_id, cta_kind, user_id),
    )


async def consume_meeting_cta(
    db: aiosqlite.Connection,
    user_id: str,
) -> None:
    """Clear the pending CTA flags after the quilt fetch has rendered them."""
    await db.execute(
        """UPDATE users SET
            memory_last_origin_id = NULL,
            memory_last_cta_kind = NULL
           WHERE id = ?""",
        (user_id,),
    )
