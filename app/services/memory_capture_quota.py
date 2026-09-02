"""Memory-capture free-tier quota helper.

End-of-meeting CQ captures (`/v1/capture-transcript`) are metered for Free
users. Lazy reset on every read/write keyed by calendar-month UTC, no
cron job. The counter and period live on `users.memory_used_this_period`
+ `users.memory_period`.

⚠ THE COUNTER IS ON THE ACCOUNT ROW, WHICH EVERY APP SHARES (SIWA issues
subject ids per developer TEAM). Under Scott's multitenancy ruling of
2026-09-02 the apps must not share anything because their users share an
identity, so `decrement_memory_quota` now REFUSES to charge this counter
on behalf of an app that does not own the lane.

Measured 2026-09-02 before adding the guard: this lane is ShoulderSurf's
alone, so nothing shared in practice and this is a tripwire rather than a
repair. That is exactly why it is worth having: the leak is latent, so the
day a second app enters the lane there would otherwise be no signal at all,
just one app's meetings quietly eating another's free captures.

The permanent fix is a per-app counter (its own table, keyed
user_id+app_id+period). Not built: these two counters use DIFFERENT period
models (this one carries `memory_period`; `generations_used` rides the
allocation cycle) and reconciling them is a migration, not a guard.

See docs/wire-contracts/memory-capture.md for the full spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from app.models.user import UserRecord
from app.services.period import current_period_utc, next_period_resets_at


logger = logging.getLogger("ghostpour.memory_quota")

# Ownership lives in ONE place, app_budget.SHARED_ACCOUNT_COUNTERS, so the
# two counters cannot drift apart on who owns them. Re-exported for readers
# and for the error line below.
from app.services.app_budget import SHARED_ACCOUNT_COUNTERS  # noqa: E402

OWNING_APP = SHARED_ACCOUNT_COUNTERS["memory_used_this_period"]


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
    app_id: str | None = None,
) -> None:
    """Atomically increment used count, materializing a fresh period if needed.

    Caller is responsible for committing the transaction. Should be called
    only when GP is processing a `capture_with_cta` outcome for a Free user.

    `app_id` is the calling app. An app that does not OWN this counter is
    refused rather than charged: sharing is the bug, so declining to charge
    is the safe direction, and the error line names the app so the follow-up
    is obvious instead of archaeological.
    """
    from app.services.app_budget import may_charge_shared_counter
    if not may_charge_shared_counter("memory_used_this_period", app_id):
        logger.error(
            "memory_quota_refused app=%s: this counter lives on the shared "
            "account row and belongs to %s. Charging it here would spend one "
            "app's free captures on another's meetings. Give %s its own "
            "counter before enabling this lane.",
            app_id, OWNING_APP, app_id,
        )
        return
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
