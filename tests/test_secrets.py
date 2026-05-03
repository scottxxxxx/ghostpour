"""Tests for app.secrets.get_secret."""

from unittest.mock import MagicMock, patch

import pytest

from app import secrets as app_secrets


@pytest.fixture(autouse=True)
def _clear_cache():
    app_secrets.get_secret.cache_clear()
    yield
    app_secrets.get_secret.cache_clear()


def test_env_var_wins_when_set(monkeypatch):
    monkeypatch.setenv("CZ_TEST_SECRET", "from-env")
    assert app_secrets.get_secret("any-name", env_var="CZ_TEST_SECRET") == "from-env"


def test_empty_env_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.setenv("CZ_TEST_SECRET", "   ")  # whitespace counts as empty
    with patch.object(app_secrets, "_from_secret_manager", return_value="from-sm") as fsm:
        result = app_secrets.get_secret("the-secret", env_var="CZ_TEST_SECRET")
    assert result == "from-sm"
    fsm.assert_called_once_with("the-secret")


def test_unset_env_var_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.delenv("CZ_TEST_SECRET", raising=False)
    with patch.object(app_secrets, "_from_secret_manager", return_value="from-sm") as fsm:
        result = app_secrets.get_secret("the-secret", env_var="CZ_TEST_SECRET")
    assert result == "from-sm"
    fsm.assert_called_once_with("the-secret")


def test_no_env_var_argument_goes_straight_to_secret_manager():
    with patch.object(app_secrets, "_from_secret_manager", return_value="direct") as fsm:
        result = app_secrets.get_secret("the-secret")
    assert result == "direct"
    fsm.assert_called_once_with("the-secret")


def test_secret_manager_failure_returns_empty_string(monkeypatch, caplog):
    monkeypatch.delenv("CZ_TEST_SECRET", raising=False)
    fake_client = MagicMock()
    fake_client.access_secret_version.side_effect = RuntimeError("boom")
    with patch.dict("sys.modules", {"google.cloud": MagicMock(secretmanager=MagicMock(SecretManagerServiceClient=lambda: fake_client))}):
        result = app_secrets.get_secret("missing", env_var="CZ_TEST_SECRET")
    assert result == ""


def test_results_are_cached(monkeypatch):
    monkeypatch.setenv("CZ_TEST_SECRET", "first")
    assert app_secrets.get_secret("name", env_var="CZ_TEST_SECRET") == "first"
    monkeypatch.setenv("CZ_TEST_SECRET", "second")
    # Cached — second call returns the cached "first" until cleared
    assert app_secrets.get_secret("name", env_var="CZ_TEST_SECRET") == "first"
    app_secrets.get_secret.cache_clear()
    assert app_secrets.get_secret("name", env_var="CZ_TEST_SECRET") == "second"


def test_cache_expires_after_ttl(monkeypatch):
    """A rotation that updates the env var (or SM) should be picked up
    within TTL seconds — no container restart required. We fake the
    monotonic clock to advance past the TTL without sleeping."""
    monkeypatch.setenv("CZ_TEST_SECRET", "before_rotation")

    fake_now = [0.0]
    monkeypatch.setattr(app_secrets.time, "monotonic", lambda: fake_now[0])

    # First call populates the cache at t=0 with TTL = _TTL_SECONDS
    assert app_secrets.get_secret("any", env_var="CZ_TEST_SECRET") == "before_rotation"

    # Rotate the env value
    monkeypatch.setenv("CZ_TEST_SECRET", "after_rotation")

    # Just before the TTL expires: still cached
    fake_now[0] = app_secrets._TTL_SECONDS - 0.001
    assert app_secrets.get_secret("any", env_var="CZ_TEST_SECRET") == "before_rotation"

    # Just past the TTL: cache misses, re-resolves, picks up rotation
    fake_now[0] = app_secrets._TTL_SECONDS + 0.001
    assert app_secrets.get_secret("any", env_var="CZ_TEST_SECRET") == "after_rotation"


def test_cache_eviction_under_pressure():
    """When more than _MAX_ENTRIES distinct keys are queried, the cache
    drops oldest first. Pin the bound."""
    # Fill the cache with N+5 distinct keys; assert the oldest are gone.
    n = app_secrets._MAX_ENTRIES + 5
    with patch.object(app_secrets, "_from_secret_manager") as fsm:
        fsm.side_effect = lambda name: f"val:{name}"
        for i in range(n):
            app_secrets.get_secret(f"sec_{i}")
    # The first 5 should have been evicted; later ones should still be there.
    with app_secrets._cache_lock:
        cached_keys = set(app_secrets._cache.keys())
    for i in range(5):
        assert (f"sec_{i}", None) not in cached_keys
    for i in range(5, n):
        assert (f"sec_{i}", None) in cached_keys
