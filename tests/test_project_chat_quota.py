"""Unit tests for the Project Chat quota helper."""

from datetime import datetime, timezone

from app.models.user import UserRecord
from app.services.project_chat_quota import (
    current_period_utc,
    next_period_resets_at,
    read_quota_state,
)


def _make_user(used: int = 0, period: str | None = None) -> UserRecord:
    return UserRecord(
        id="u1",
        apple_sub="apple_sub_u1",
        tier="free",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        project_chat_used_this_period=used,
        project_chat_period=period,
    )


def test_current_period_utc_format():
    fixed = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    assert current_period_utc(fixed) == "2026-04"


def test_next_period_resets_at_rolls_to_first_of_next_month():
    fixed = datetime(2026, 4, 15, 18, 30, 0, tzinfo=timezone.utc)
    assert next_period_resets_at(fixed).startswith("2026-05-01T00:00:00")


def test_next_period_resets_at_handles_december_rollover():
    fixed = datetime(2026, 12, 31, 23, 0, 0, tzinfo=timezone.utc)
    assert next_period_resets_at(fixed).startswith("2027-01-01T00:00:00")


def test_read_quota_state_fresh_period_returns_full_quota():
    """User with current period stamp + 0 used → full quota remaining."""
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=0, period="2026-04")
    state = read_quota_state(user, free_quota_per_month=3, now=fixed)
    assert state.used == 0
    assert state.total == 3
    assert state.remaining == 3
    assert state.has_quota is True


def test_read_quota_state_partial_use():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=2, period="2026-04")
    state = read_quota_state(user, free_quota_per_month=3, now=fixed)
    assert state.remaining == 1
    assert state.has_quota is True


def test_read_quota_state_exhausted():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=3, period="2026-04")
    state = read_quota_state(user, free_quota_per_month=3, now=fixed)
    assert state.remaining == 0
    assert state.has_quota is False


def test_read_quota_state_lazy_reset_on_stale_period():
    """User with stale period stamp gets virtual reset on read — no DB write."""
    fixed = datetime(2026, 5, 1, 0, 5, 0, tzinfo=timezone.utc)
    user = _make_user(used=3, period="2026-04")  # stale
    state = read_quota_state(user, free_quota_per_month=3, now=fixed)
    assert state.used == 0  # virtual reset
    assert state.remaining == 3
    assert state.has_quota is True
    assert state.resets_at.startswith("2026-06-01")


def test_read_quota_state_unlimited_returns_remaining_none():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=42, period="2026-04")
    state = read_quota_state(user, free_quota_per_month=-1, now=fixed)
    assert state.total == -1
    assert state.remaining is None
    assert state.has_quota is True


def test_read_quota_state_zero_quota_always_exhausted():
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=0, period="2026-04")
    state = read_quota_state(user, free_quota_per_month=0, now=fixed)
    assert state.total == 0
    assert state.remaining == 0
    assert state.has_quota is False


def test_read_quota_state_null_period_treated_as_stale():
    """Brand-new user has null period — treated as fresh quota."""
    fixed = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    user = _make_user(used=0, period=None)
    state = read_quota_state(user, free_quota_per_month=3, now=fixed)
    assert state.used == 0
    assert state.remaining == 3
