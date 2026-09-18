"""A missing default CQ app id has to be loud at startup, because it is silent
at runtime.

CQ's `applications.app_id` is a uuid column. A blank or non-UUID client_id makes
asyncpg raise inside their lookup, so the request takes their outer exception arm
and never reaches verify_password or their failure counter. On our side
`_get_auth_headers` falls back to the `X-App-ID` header, and the cooldown that
would normally shout keys on 400/401/403, so it never fires. Neither end holds
evidence that anything is wrong. The only moment the value is knowable before
anything has been sent is startup.

The old code default was the literal "cloudzap", which is not UUID shaped and so
was never valid against that column. It was never in force (prod sets the env
var), which is exactly what made it dangerous: a default nobody reviews,
reachable only through a dropped env var, the case nobody tests.
"""
import logging
import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def cq_env(tmp_path) -> Generator[dict, None, None]:
    """The app_env fixture, but with CZ_CQ_APP_ID left to the caller."""
    env = {
        "CZ_JWT_SECRET": "test-secret-key-that-is-long-enough-for-hs256-validation",
        "CZ_APPLE_BUNDLE_ID": "com.test.app",
        "CZ_ADMIN_KEY": "test-admin-key",
        "CZ_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "CZ_CQ_BASE_URL": "http://cq-mock:8000",
        "CZ_CQ_RECALL_TIMEOUT_MS": "200",
    }
    old = {k: os.environ.get(k) for k in list(env) + ["CZ_CQ_APP_ID"]}
    os.environ.update(env)

    from app.config import get_settings
    get_settings.cache_clear()
    yield env
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _boot_and_capture() -> list[logging.LogRecord]:
    """Boot the real app through lifespan and return the cq_app_id_missing lines.

    ⚠ NOT caplog. `app/main.py` calls `logging.basicConfig(..., force=True)` at
    IMPORT time, and force=True removes every existing root handler, pytest's
    included. caplog.records came back empty here while the line was plainly on
    stderr, which reads exactly like a check that did not fire. Attach our own
    handler to the logger that emits, after the import that would have dropped it.
    """
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app

    logger = logging.getLogger("app.main")
    collector = _Collect()
    logger.addHandler(collector)
    try:
        with TestClient(app, raise_server_exceptions=False):
            pass
    finally:
        logger.removeHandler(collector)
    return [r for r in collector.records if "cq_app_id_missing" in r.getMessage()]


def test_the_code_default_is_empty_and_not_a_string_cq_never_accepted():
    """Guards the default itself. A future edit that puts any literal back here
    reintroduces the silent path, whatever the literal is."""
    from app.config import Settings
    assert Settings.model_fields["cq_app_id"].default == "", (
        "cq_app_id must have NO default: any literal here is an assertion that "
        "the string is a valid CQ app id, and CQ's column is a uuid"
    )


def test_a_missing_app_id_is_loud_at_startup(cq_env, mock_provider, mock_pricing):
    """The whole point: boot with CQ configured and no app id, and the operator
    is told, at ERROR, before a single call has gone out."""
    os.environ["CZ_CQ_APP_ID"] = ""
    hits = _boot_and_capture()
    assert len(hits) == 1, (
        f"expected exactly one cq_app_id_missing line at startup, got {len(hits)}"
    )
    msg = hits[0].getMessage()
    assert hits[0].levelno == logging.ERROR
    assert "CZ_CQ_APP_ID" in msg, "the line must name the variable an operator sets"
    assert "X-App-ID" in msg, "the line must name the silent behaviour it predicts"


def test_a_configured_app_id_says_nothing(cq_env, mock_provider, mock_pricing):
    """The other direction. A check that fires on a healthy boot is noise, and
    noise at ERROR is how a real line gets skimmed past."""
    os.environ["CZ_CQ_APP_ID"] = "11111111-2222-3333-4444-555555555555"
    assert _boot_and_capture() == []


def test_no_cq_at_all_says_nothing(cq_env, mock_provider, mock_pricing):
    """A deployment with no CQ integration has no default identity to be missing,
    so the check must not fire on it."""
    os.environ["CZ_CQ_APP_ID"] = ""
    os.environ["CZ_CQ_BASE_URL"] = ""
    assert _boot_and_capture() == []
