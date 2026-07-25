"""Whale alert (docs/decisions/cost-and-limits.md, 2026-07-25): ops
incident when a user's month-to-date usage_log cost crosses the
threshold. One open incident per user per month; below-threshold and
disabled configurations stay silent; failures never reach the request
path.
"""

import datetime
import sqlite3

import pytest

from tests.conftest import _insert_user

_NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()


def _add_usage(db_path: str, user_id: str, cost: float, n: int = 1,
               call_type: str = "query") -> None:
    conn = sqlite3.connect(db_path)
    for i in range(n):
        conn.execute(
            "INSERT INTO usage_log (id, user_id, provider, model, "
            "estimated_cost_usd, request_timestamp, status, call_type) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"u-{user_id}-{cost}-{i}", user_id, "anthropic", "m",
             cost, _NOW, "success", call_type))
    conn.commit()
    conn.close()


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _incidents(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT category, subject FROM alert_incidents").fetchall()
    finally:
        conn.close()


@pytest.mark.anyio
async def test_whale_fires_once_and_dedups(client, app_env, monkeypatch):
    import aiosqlite

    from app.config import get_settings
    from app.services.cost_alerts import check_whale
    monkeypatch.setattr(get_settings(), "cost_alert_threshold_usd", 10.0)
    _insert_user(_db(app_env), "whale-user", tier="pro")
    _add_usage(_db(app_env), "whale-user", 6.0, n=2, call_type="meeting_chat")

    async with aiosqlite.connect(_db(app_env)) as db:
        db.row_factory = aiosqlite.Row
        await check_whale(db, "whale-user")
        await check_whale(db, "whale-user")   # dedup: still one incident
    rows = _incidents(_db(app_env))
    assert len(rows) == 1
    assert rows[0][0] == "user_cost_whale"
    assert rows[0][1].startswith("whale-us:")


@pytest.mark.anyio
async def test_below_threshold_and_disabled_stay_silent(client, app_env, monkeypatch):
    import aiosqlite

    from app.config import get_settings
    from app.services.cost_alerts import check_whale
    _insert_user(_db(app_env), "small-user")
    _add_usage(_db(app_env), "small-user", 1.0)

    async with aiosqlite.connect(_db(app_env)) as db:
        db.row_factory = aiosqlite.Row
        monkeypatch.setattr(get_settings(), "cost_alert_threshold_usd", 10.0)
        await check_whale(db, "small-user")
        monkeypatch.setattr(get_settings(), "cost_alert_threshold_usd", 0)
        _add_usage(_db(app_env), "small-user", 50.0)
        await check_whale(db, "small-user")   # disabled: silent at any cost
    assert _incidents(_db(app_env)) == []


@pytest.mark.anyio
async def test_whale_check_never_raises(client, app_env, monkeypatch):
    import aiosqlite

    from app.config import get_settings
    from app.services.cost_alerts import check_whale
    monkeypatch.setattr(get_settings(), "cost_alert_threshold_usd", 10.0)
    _insert_user(_db(app_env), "boom-user")
    _add_usage(_db(app_env), "boom-user", 20.0)

    async def _boom(*a, **k):
        raise RuntimeError("smtp down")

    import app.services.alerting as alerting
    monkeypatch.setattr(alerting, "report_incident", _boom)
    async with aiosqlite.connect(_db(app_env)) as db:
        db.row_factory = aiosqlite.Row
        await check_whale(db, "boom-user")    # swallowed, logged
