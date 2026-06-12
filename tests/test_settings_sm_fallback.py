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
    """When CZ_FOO is already set, the env value wins for the Settings
    field regardless of what SM holds. (SM is now consulted read-only for
    the shadow check — see test_env_shadows_sm_warns — but it must never
    override the env value.)"""
    monkeypatch.setenv("CZ_ANTHROPIC_API_KEY", "from-env-sk-ant")
    with patch("app.secrets.get_secret") as gs:
        gs.return_value = "from-sm-MUST-NOT-WIN"
        app_config.get_settings.cache_clear()
        s = app_config.get_settings()

    assert s.anthropic_api_key == "from-env-sk-ant"


def test_env_shadows_sm_warns(monkeypatch, caplog):
    """Env set + SM holds a DIFFERENT value → loud env_shadows_sm warning.
    This is the live-rotation-reverts trap: the operator rotated the key
    into SM but a stale .env entry still wins at startup."""
    import logging
    monkeypatch.setenv("CZ_ANTHROPIC_API_KEY", "stale-env-key")

    def fake_get_secret(name, env_var=None):
        return "freshly-rotated-sm-key" if name == "anthropic-api-key" else ""

    with patch("app.secrets.get_secret", side_effect=fake_get_secret):
        with caplog.at_level(logging.WARNING, logger="app.config"):
            app_config.get_settings.cache_clear()
            s = app_config.get_settings()

    assert s.anthropic_api_key == "stale-env-key"  # env still wins
    shadow_warnings = [r for r in caplog.records if "env_shadows_sm" in r.getMessage()]
    assert any("CZ_ANTHROPIC_API_KEY" in r.getMessage() for r in shadow_warnings)
    # Redaction: neither secret value may appear in the log line.
    for r in shadow_warnings:
        assert "stale-env-key" not in r.getMessage()
        assert "freshly-rotated-sm-key" not in r.getMessage()


def test_no_shadow_warning_when_env_matches_sm(monkeypatch, caplog):
    """Env set + SM holds the SAME value → no warning (already consistent;
    nothing is being shadowed)."""
    import logging
    monkeypatch.setenv("CZ_ANTHROPIC_API_KEY", "same-key")

    def fake_get_secret(name, env_var=None):
        return "same-key" if name == "anthropic-api-key" else ""

    with patch("app.secrets.get_secret", side_effect=fake_get_secret):
        with caplog.at_level(logging.WARNING, logger="app.config"):
            app_config.get_settings.cache_clear()
            app_config.get_settings()

    assert not [r for r in caplog.records if "env_shadows_sm" in r.getMessage()]


def test_no_shadow_warning_when_sm_unreachable(monkeypatch, caplog):
    """Env set + SM returns "" (unreachable / secret absent) → no false
    warning. We only warn on a genuine divergence."""
    import logging
    monkeypatch.setenv("CZ_ANTHROPIC_API_KEY", "env-only-key")

    with patch("app.secrets.get_secret", return_value=""):
        with caplog.at_level(logging.WARNING, logger="app.config"):
            app_config.get_settings.cache_clear()
            app_config.get_settings()

    assert not [r for r in caplog.records if "env_shadows_sm" in r.getMessage()]


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
    because the default is provided in the class definition.

    NOTE: use `setenv("", "")` (empty string) rather than `delenv`.
    Pydantic-settings reads from `.env` as a fallback, so deleting
    from os.environ alone lets it fall through to the developer's
    real `.env` file — which can leak a real key into a test
    AssertionError if the test fails. Setting to empty string keeps
    pydantic on env-resolution and skips the file fallback.

    Same fix needs to apply anywhere a test asserts a settings field
    is empty/None — see feedback_env_dump_redaction memory."""
    monkeypatch.setenv("CZ_OPENAI_API_KEY", "")
    with patch("app.secrets.get_secret", return_value=""):
        app_config.get_settings.cache_clear()
        s = app_config.get_settings()

    assert s.openai_api_key == ""


def test_ensure_is_idempotent(monkeypatch):
    """Running _ensure_secrets_in_env twice doesn't blow up or re-mutate
    the env value. Pin so a future caller can call it whenever without
    worry.

    The second call no longer skips SM entirely: with env now populated it
    takes the shadow-check path and re-reads SM to compare. In production
    that read is served from get_secret's TTL cache, so it's not an extra
    round-trip; here the mock bypasses the cache, hence two fetches. The
    important idempotency guarantee — the env value is set once and not
    overwritten — still holds."""
    monkeypatch.delenv("CZ_KIMI_API_KEY", raising=False)
    fetch_count = {"n": 0}

    def fake_get_secret(name, env_var=None):
        if name == "kimi-api-key":
            fetch_count["n"] += 1
            return "kimi-sm-value"
        return ""

    with patch("app.secrets.get_secret", side_effect=fake_get_secret):
        app_config._ensure_secrets_in_env()  # env empty → fill from SM
        app_config._ensure_secrets_in_env()  # env set → shadow check only

    import os
    # 1 fill + 1 shadow-check read (matching value → no warning, no re-mutate)
    assert fetch_count["n"] == 2
    assert os.environ.get("CZ_KIMI_API_KEY") == "kimi-sm-value"


def test_secret_resolution_summary_buckets(monkeypatch, caplog):
    """Boot emits exactly one secret_resolution_summary INFO line that
    buckets every mapped secret by where it resolved from — the single
    line of ground truth that prevents misreading per-secret 404s as
    'Secret Manager is down'. Names only, never values."""
    import logging
    import re

    monkeypatch.setenv("CZ_JWT_SECRET", "env-value")   # → env_resident
    monkeypatch.setenv("CZ_ANTHROPIC_API_KEY", "")     # → filled_from_sm
    monkeypatch.setenv("CZ_KIMI_API_KEY", "")          # → no_value

    def fake_get_secret(name, env_var=None):
        return "sm-value" if name == "anthropic-api-key" else ""

    with patch("app.secrets.get_secret", side_effect=fake_get_secret):
        with caplog.at_level(logging.INFO, logger="app.config"):
            app_config._ensure_secrets_in_env()

    summaries = [r.getMessage() for r in caplog.records
                 if "secret_resolution_summary" in r.getMessage()]
    assert len(summaries) == 1
    m = re.search(
        r"filled_from_sm=\[([^\]]*)\] env_resident=\[([^\]]*)\] no_value=\[([^\]]*)\]",
        summaries[0],
    )
    assert m, summaries[0]
    assert "anthropic-api-key" in m.group(1).split(",")
    assert "jwt-secret" in m.group(2).split(",")
    assert "kimi-api-key" in m.group(3).split(",")
    # Redaction: no values in the summary.
    assert "env-value" not in summaries[0]
    assert "sm-value" not in summaries[0]


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
