"""Calendar-month period helpers shared by feature-quota services.

Both memory-capture (and previously Project Chat) keyed their lazy-reset
counters off a UTC calendar-month string. The helpers live here so the
quota services can drop their inter-imports.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone


def current_period_utc(now: datetime | None = None) -> str:
    """Return the current calendar-month period key in UTC, e.g. '2026-04'."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def next_period_resets_at(now: datetime | None = None) -> str:
    """ISO timestamp of the upcoming month boundary (UTC midnight on the 1st)."""
    now = now or datetime.now(timezone.utc)
    last_day = calendar.monthrange(now.year, now.month)[1]
    end_of_month = now.replace(
        day=last_day, hour=23, minute=59, second=59, microsecond=999999
    )
    next_first = (end_of_month + timedelta(microseconds=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_first.isoformat().replace("+00:00", "Z")
