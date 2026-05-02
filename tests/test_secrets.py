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
