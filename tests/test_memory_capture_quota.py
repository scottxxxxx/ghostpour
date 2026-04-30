"""Unit tests for the memory-capture quota helper."""

from datetime import datetime, timezone

from app.models.user import UserRecord
from app.services.memory_capture_quota import read_memory_quota_state


def _make_user(used: int = 0, period: str | None = None) -> UserRecord:
    return UserRecord(
        id="u1",
        apple_sub="apple_sub_u1",
        tier="free",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        memory_used_this_period=used,
        memory_period=period,
    )


def test_fresh_period_returns_full_quota():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=0, period="2026-04")
    state = read_memory_quota_state(user, free_quota_per_month=1, now=fixed)
    assert state.used == 0
    assert state.total == 1
    assert state.remaining == 1
    assert state.has_quota is True


def test_quota_exhausted():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=1, period="2026-04")
    state = read_memory_quota_state(user, free_quota_per_month=1, now=fixed)
    assert state.remaining == 0
    assert state.has_quota is False


def test_lazy_reset_on_stale_period():
    """User with stale period stamp gets virtual reset on read — no DB write."""
    fixed = datetime(2026, 5, 1, 0, 5, 0, tzinfo=timezone.utc)
    user = _make_user(used=1, period="2026-04")  # stale, prior month
    state = read_memory_quota_state(user, free_quota_per_month=1, now=fixed)
    assert state.used == 0
    assert state.has_quota is True


def test_unlimited_returns_remaining_none():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=42, period="2026-04")
    state = read_memory_quota_state(user, free_quota_per_month=-1, now=fixed)
    assert state.total == -1
    assert state.remaining is None
    assert state.has_quota is True


def test_zero_quota_always_exhausted():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=0, period="2026-04")
    state = read_memory_quota_state(user, free_quota_per_month=0, now=fixed)
    assert state.has_quota is False


def test_null_period_treated_as_fresh():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=0, period=None)
    state = read_memory_quota_state(user, free_quota_per_month=1, now=fixed)
    assert state.used == 0
    assert state.has_quota is True
