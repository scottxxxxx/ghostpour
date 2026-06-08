"""Tests for app/services/allocation_reset_sweep.py.

The sweep applies lazy_reset_if_due to every active user whose
allocation_resets_at has passed, so INACTIVE users (who never hit the
usage path) still get their stale monthly_used_usd counter zeroed at the
period boundary. Without it, an inactive user stuck at/over their limit
produces a permanent false allocation alert on the Overview dashboard —
observed in prod with a user pinned at $0.35/$0.35 = 100% with zero
usage_log rows.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import aiosqlite
import pytest

from app.services.allocation_reset_sweep import sweep_due_allocations


def _seed_user(
    db_path: str,
    *,
    user_id: str,
    monthly_used: float,
    resets_at: str | None,
    is_active: int = 1,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, display_name, tier, created_at, updated_at,
            is_active, monthly_cost_limit_usd, monthly_used_usd,
            overage_balance_usd, allocation_resets_at, is_trial)
           VALUES (?, ?, ?, ?, 'free', ?, ?, ?, 0.35, ?, 0, ?, 0)""",
        (
            user_id,
            f"sub_{user_id}",
            f"{user_id}@test.com",
            "Test",
            now,
            now,
            is_active,
            monthly_used,
            resets_at,
        ),
    )
    conn.commit()
    conn.close()


def _used(db_path: str, user_id: str) -> float:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT monthly_used_usd FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row[0]


@pytest.mark.asyncio
async def test_sweep_resets_due_inactive_user(client, tmp_db_path):
    """The core bug: a user past their reset date who never hit the usage
    path still gets reset by the sweep."""
    past = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    _seed_user(tmp_db_path, user_id="stuck", monthly_used=0.35, resets_at=past)

    now = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        n = await sweep_due_allocations(db, now=now)

    assert n == 1
    assert _used(tmp_db_path, "stuck") == 0


@pytest.mark.asyncio
async def test_sweep_skips_not_due_user(client, tmp_db_path):
    """A user whose reset date is in the future is left untouched."""
    future = "2099-01-01T00:00:00+00:00"
    _seed_user(tmp_db_path, user_id="fresh", monthly_used=0.20, resets_at=future)

    now = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        n = await sweep_due_allocations(db, now=now)

    assert n == 0
    assert _used(tmp_db_path, "fresh") == 0.20


@pytest.mark.asyncio
async def test_sweep_skips_inactive_and_null_resets(client, tmp_db_path):
    """is_active=0 users and users with NULL allocation_resets_at are not
    candidates — the SELECT filters both out."""
    past = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    _seed_user(tmp_db_path, user_id="inactive", monthly_used=0.35, resets_at=past, is_active=0)
    _seed_user(tmp_db_path, user_id="nullreset", monthly_used=0.35, resets_at=None)

    now = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        n = await sweep_due_allocations(db, now=now)

    assert n == 0
    assert _used(tmp_db_path, "inactive") == 0.35  # untouched
    assert _used(tmp_db_path, "nullreset") == 0.35


@pytest.mark.asyncio
async def test_sweep_mixed_population_counts_only_reset(client, tmp_db_path):
    """A realistic mix: only the active, due, non-null users count."""
    past = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    future = "2099-01-01T00:00:00+00:00"
    _seed_user(tmp_db_path, user_id="due1", monthly_used=0.35, resets_at=past)
    _seed_user(tmp_db_path, user_id="due2", monthly_used=0.10, resets_at=past)
    _seed_user(tmp_db_path, user_id="future", monthly_used=0.05, resets_at=future)
    _seed_user(tmp_db_path, user_id="off", monthly_used=0.35, resets_at=past, is_active=0)

    now = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        n = await sweep_due_allocations(db, now=now)

    assert n == 2
    assert _used(tmp_db_path, "due1") == 0
    assert _used(tmp_db_path, "due2") == 0
    assert _used(tmp_db_path, "future") == 0.05
    assert _used(tmp_db_path, "off") == 0.35


@pytest.mark.asyncio
async def test_sweep_empty_population_returns_zero(client, tmp_db_path):
    """No users → no crash, returns 0."""
    now = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    async with aiosqlite.connect(tmp_db_path) as db:
        n = await sweep_due_allocations(db, now=now)
    assert n == 0
