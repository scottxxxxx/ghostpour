"""Tests for app/services/allocation_reset.py + the Apple webhook
same-tier renewal path that historically didn't reset anything.

Two concrete bugs being pinned:

1. The 30-day drift: every site that locally computed `now + 30 days`
   for `allocation_resets_at` would drift ~5 days/year vs Apple's actual
   calendar-month billing cycle. After this fix, locally-computed resets
   use `relativedelta(months=1)` and Apple-aware sites prefer
   `expiresDate` from the signed transaction.

2. The same-tier renewal early-return: `apple_webhooks.py` used to log
   and return without resetting `monthly_used_usd` or advancing
   `allocation_resets_at` when a user renewed the SAME tier they were
   already on (i.e., normal monthly Plus → Plus renewal). After 30 days
   their cycle would freeze and `monthly_used_usd` would accumulate
   forever. This test pins the new reset behavior.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import aiosqlite
import pytest

from app.services.allocation_reset import (
    compute_next_reset,
    lazy_reset_if_due,
    parse_iso,
    roll_forward_past,
)


# ---------------------------------------------------------------------------
# compute_next_reset
# ---------------------------------------------------------------------------


def test_compute_next_reset_with_apple_expires_date_uses_apple_value():
    """When Apple gives us expiresDate, that's the source of truth — we
    don't second-guess with our own calendar math."""
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    # Apple's expiresDate is in milliseconds since epoch
    expires_ms = int(datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    result = compute_next_reset(now, apple_expires_date_ms=expires_ms)
    assert result == datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_compute_next_reset_without_apple_expires_date_uses_one_calendar_month():
    """No Apple data → fall back to `now + 1 calendar month`. NOT 30 days,
    which was the legacy behavior that drifted ~5 days/year."""
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    result = compute_next_reset(now)
    assert result == datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_compute_next_reset_handles_jan_31_to_feb_28():
    """Calendar edge case: Jan 31 → Feb 28 (no Feb 31 exists). dateutil
    snaps back to last day of target month, matching Apple's behavior."""
    now = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = compute_next_reset(now)
    assert result == datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)


def test_compute_next_reset_handles_jan_31_to_feb_29_in_leap_year():
    """Same edge case but in a leap year — Feb 29 should be used."""
    now = datetime(2024, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = compute_next_reset(now)
    assert result == datetime(2024, 2, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_compute_next_reset_apple_value_takes_precedence_even_when_weird():
    """If Apple says reset is in 47 days, we trust them — they know their
    grace periods, billing retries, etc. better than we do."""
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    apple_says = datetime(2026, 6, 30, 8, 30, 0, tzinfo=timezone.utc)
    result = compute_next_reset(
        now,
        apple_expires_date_ms=int(apple_says.timestamp() * 1000),
    )
    assert result == apple_says


# ---------------------------------------------------------------------------
# roll_forward_past — preserves the user's day-of-month anchor across a gap
# ---------------------------------------------------------------------------


def test_roll_forward_past_single_month_gap():
    """One month past — bump exactly once."""
    stale = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    result = roll_forward_past(stale, now)
    assert result == datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_roll_forward_past_multi_month_gap():
    """Free user inactive for 3 months returns — should bump 4 times so
    the next-reset is strictly after `now`, while preserving the day-of-
    month anchor (still resets on the 15th, just 4 months later)."""
    stale = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    result = roll_forward_past(stale, now)
    assert result == datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert result > now


def test_roll_forward_past_preserves_end_of_month_anchor():
    """A user anchored on Jan 31 stays on the last-day-of-month after
    rolling through Feb (which doesn't have a 31st)."""
    stale = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    result = roll_forward_past(stale, now)
    # 1/31 → 2/28 → 3/31 → 4/30 → 5/31 (first one strictly after now)
    assert result == datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# parse_iso — defensive parsing helper
# ---------------------------------------------------------------------------


def test_parse_iso_none_returns_none():
    assert parse_iso(None) is None


def test_parse_iso_empty_returns_none():
    assert parse_iso("") is None


def test_parse_iso_malformed_returns_none():
    assert parse_iso("not a date") is None


def test_parse_iso_valid_z_suffix():
    """ISO strings with `Z` UTC suffix should parse to aware datetimes."""
    result = parse_iso("2026-05-14T12:00:00Z")
    assert result == datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_naive_treated_as_utc():
    """Naive ISO strings (no offset) are treated as UTC — matches our
    convention of storing UTC ISO timestamps in SQLite."""
    result = parse_iso("2026-05-14T12:00:00")
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# lazy_reset_if_due — the safety-net path used at every budget check
# ---------------------------------------------------------------------------


def _seed_user(
    db_path: str,
    *,
    user_id: str = "lazy-test-user",
    monthly_used: float = 1.50,
    resets_at: str = "2099-01-01T00:00:00Z",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, display_name, tier, created_at, updated_at,
            is_active, monthly_cost_limit_usd, monthly_used_usd,
            overage_balance_usd, allocation_resets_at, is_trial)
           VALUES (?, ?, ?, ?, 'plus', ?, ?, 1, 5.00, ?, 0, ?, 0)""",
        (
            user_id,
            f"sub_{user_id}",
            f"{user_id}@test.com",
            "Test",
            now,
            now,
            monthly_used,
            resets_at,
        ),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_lazy_reset_not_due_does_nothing(client, tmp_db_path):
    """When `allocation_resets_at` is still in the future, no reset
    fires and counters stay where they were."""
    _seed_user(tmp_db_path, monthly_used=2.50, resets_at="2099-01-01T00:00:00Z")

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        did_reset = await lazy_reset_if_due(db, "lazy-test-user")
        assert did_reset is False

        cursor = await db.execute(
            "SELECT monthly_used_usd, allocation_resets_at FROM users WHERE id = ?",
            ("lazy-test-user",),
        )
        row = await cursor.fetchone()
        assert row["monthly_used_usd"] == 2.50
        assert row["allocation_resets_at"] == "2099-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_lazy_reset_due_zeros_counters_and_advances(client, tmp_db_path):
    """When the reset date has passed, monthly_used drops to 0 and
    allocation_resets_at is rolled forward at least one month past now."""
    # Anchor 2 months ago — should roll forward twice
    past = datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    _seed_user(tmp_db_path, monthly_used=4.99, resets_at=past)

    now = datetime(2026, 5, 5, 14, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        did_reset = await lazy_reset_if_due(db, "lazy-test-user", now=now)
        assert did_reset is True

        cursor = await db.execute(
            "SELECT monthly_used_usd, allocation_resets_at FROM users WHERE id = ?",
            ("lazy-test-user",),
        )
        row = await cursor.fetchone()
        assert row["monthly_used_usd"] == 0
        # 3/5 -> 4/5 -> 5/5 -> 6/5 (first strictly past 5/5 14:00)
        assert row["allocation_resets_at"] == "2026-06-05T12:00:00+00:00"


@pytest.mark.asyncio
async def test_lazy_reset_idempotent_on_repeat_call(client, tmp_db_path):
    """First call resets and bumps the date. A second call against the
    same already-bumped row is not due → no double-reset, no double-bump."""
    past = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    _seed_user(tmp_db_path, monthly_used=3.00, resets_at=past)

    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        first = await lazy_reset_if_due(db, "lazy-test-user", now=now)
        second = await lazy_reset_if_due(db, "lazy-test-user", now=now)
        assert first is True
        assert second is False


@pytest.mark.asyncio
async def test_lazy_reset_unknown_user_returns_false(client, tmp_db_path):
    """No row → return False, don't crash."""
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        result = await lazy_reset_if_due(db, "no-such-user-id")
        assert result is False


@pytest.mark.asyncio
async def test_lazy_reset_null_resets_at_returns_false(client, tmp_db_path):
    """Defensive: a row with NULL `allocation_resets_at` should not
    explode and should not be treated as 'due forever'. We treat null as
    'don't auto-reset', leaving it for the explicit set paths to
    populate (verify-receipt / Apple webhook / admin)."""
    _seed_user(tmp_db_path, monthly_used=1.00, resets_at="2099-01-01T00:00:00Z")

    # Now overwrite resets_at to NULL via direct SQL (simulating an
    # old row that pre-dates the column or had it cleared)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE users SET allocation_resets_at = NULL WHERE id = ?",
        ("lazy-test-user",),
    )
    conn.commit()
    conn.close()

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        result = await lazy_reset_if_due(db, "lazy-test-user")
        assert result is False


# ---------------------------------------------------------------------------
# Apple webhook same-tier renewal: pins the bug fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apple_renew_same_tier_resets_counters_and_advances(client, tmp_db_path):
    """Pre-fix: a Plus user renewing Plus hit the early-return path,
    which logged and returned without touching the user row. Counters
    accumulated across renewals indefinitely. Post-fix: same-tier renewal
    runs `_renew_same_tier`, which zeros monthly_used and advances
    allocation_resets_at to Apple's `expiresDate`."""
    from app.routers.apple_webhooks import _renew_same_tier

    _seed_user(
        tmp_db_path,
        monthly_used=4.50,
        resets_at="2026-05-14T12:00:00+00:00",
    )

    # Apple says the next renewal is June 14 (calendar-anniversary, no
    # 30-day approximation). expiresDate is in ms since epoch.
    next_renewal = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    expires_ms = int(next_renewal.timestamp() * 1000)

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _renew_same_tier(db, "lazy-test-user", expires_ms)

        cursor = await db.execute(
            "SELECT monthly_used_usd, allocation_resets_at FROM users WHERE id = ?",
            ("lazy-test-user",),
        )
        row = await cursor.fetchone()
        assert row["monthly_used_usd"] == 0
        # Anchored exactly on Apple's expiresDate, NOT now+30d
        assert row["allocation_resets_at"] == next_renewal.isoformat()


@pytest.mark.asyncio
async def test_apple_renew_without_expires_date_falls_back_to_one_month(
    client, tmp_db_path
):
    """Defensive: if Apple's transaction info is missing `expiresDate`
    (unexpected, but can happen for sandbox/edge cases), we should still
    reset and advance — using the local 1-calendar-month fallback rather
    than failing or leaving the cycle frozen."""
    from app.routers.apple_webhooks import _renew_same_tier

    _seed_user(
        tmp_db_path,
        monthly_used=2.00,
        resets_at="2026-05-14T12:00:00+00:00",
    )

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _renew_same_tier(db, "lazy-test-user", apple_expires_date_ms=None)

        cursor = await db.execute(
            "SELECT monthly_used_usd, allocation_resets_at FROM users WHERE id = ?",
            ("lazy-test-user",),
        )
        row = await cursor.fetchone()
        assert row["monthly_used_usd"] == 0
        # Resets advanced to ~1 month from "now" — exact value depends on
        # wall-clock, so just assert it's at least a few days in the future
        new_reset = datetime.fromisoformat(row["allocation_resets_at"])
        delta = new_reset - datetime.now(timezone.utc)
        assert delta.days >= 25  # ~1 month, allowing slack
        assert delta.days <= 32
