"""Pin the env-then-SM fallback behavior introduced in C5.

The fallback runs at `get_settings()` time via `_ensure_secrets_in_env`.
For each `CZ_<X>` mapping, if env is empty, fetch from SM and set
os.environ. Then pydantic Settings loads normally.

Pin both the happy path (env empty + SM has value → field populated)
and the no-op (env present → SM never consulted)."""

from __future__ import annotations

from unittest.mock import patch

from app import config as app_config


def test_env_value_wins_over_sm(monkeypatch):
    """When CZ_FOO is already set, _ensure_secrets_in_env doesn't even
    look at SM for that mapping."""
    monkeypatch.setenv("CZ_ANTHROPIC_API_KEY", "from-env-sk-ant")
    with patch("app.secrets.get_secret") as gs:
        # Important: get_secret should NOT be called for this key.
        gs.return_value = "from-sm-MUST-NOT-WIN"
        app_config.get_settings.cache_clear()
        s = app_config.get_settings()

    assert s.anthropic_api_key == "from-env-sk-ant"
    # get_secret may still be called for OTHER mappings — assert this one
    # specifically wasn't asked for.
    called_secret_names = [c.args[0] for c in gs.call_args_list]
    assert "anthropic-api-key" not in called_secret_names


def test_sm_fills_in_when_env_empty(monkeypatch):
    """The migration path: CZ_FOO unset → SM provides → Settings field
    is populated with the SM value."""
    monkeypatch.delenv("CZ_ANTHROPIC_API_KEY", raising=False)

    def fake_get_secret(name, env_var=None):
        return "from-sm-success" if name == "anthropic-api-key" else ""

    with patch("app.secrets.get_secret", side_effect=fake_get_secret):
        app_config.get_settings.cache_clear()
        s = app_config.get_settings()

    assert s.anthropic_api_key == "from-sm-success"


def test_no_match_leaves_field_empty(monkeypatch):
    """If neither env nor SM has the value, the field stays at its
    default ("" for empty-default fields). pydantic doesn't complain
    because the default is provided in the class definition."""
    monkeypatch.delenv("CZ_OPENAI_API_KEY", raising=False)
    with patch("app.secrets.get_secret", return_value=""):
        app_config.get_settings.cache_clear()
        s = app_config.get_settings()

    assert s.openai_api_key == ""


def test_ensure_is_idempotent(monkeypatch):
    """Running _ensure_secrets_in_env twice doesn't blow up or mutate
    twice. Pin so a future caller can call it whenever without worry."""
    monkeypatch.delenv("CZ_KIMI_API_KEY", raising=False)
    fetch_count = {"n": 0}

    def fake_get_secret(name, env_var=None):
        if name == "kimi-api-key":
            fetch_count["n"] += 1
            return "kimi-sm-value"
        return ""

    with patch("app.secrets.get_secret", side_effect=fake_get_secret):
        app_config._ensure_secrets_in_env()
        # After first call, env now has the value → second call is no-op
        # for this mapping.
        app_config._ensure_secrets_in_env()

    import os
    assert fetch_count["n"] == 1
    assert os.environ.get("CZ_KIMI_API_KEY") == "kimi-sm-value"


def test_mapping_does_not_include_non_secret_config():
    """`CZ_GCP_PROJECT`, `CZ_DATABASE_URL`, `CZ_APPLE_BUNDLE_ID` etc.
    are configuration, not secrets — keep them out of the SM fallback
    surface so the surface stays auditable as 'these are the actual
    secrets' going forward."""
    forbidden = [
        "CZ_GCP_PROJECT",
        "CZ_DATABASE_URL",
        "CZ_APPLE_BUNDLE_ID",
        "CZ_CQ_BASE_URL",
        "CZ_CQ_APP_ID",
    ]
    for k in forbidden:
        assert k not in app_config._SECRET_MANAGER_MAPPINGS, (
            f"{k} is configuration, not a secret — don't add it to "
            "_SECRET_MANAGER_MAPPINGS. Update the test if intentional."
        )
