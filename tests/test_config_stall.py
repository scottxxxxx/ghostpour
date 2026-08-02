"""Poisoned-config-cache detection (2026-08-02).

SS confirmed that a config which fails to decode causes iOS to delete the
cache and fall back to the copy bundled in the app, then refetch and fail
again on every launch, forever, with no telemetry. From the server that
looks like one thing and nothing else looks like it: a client that keeps
taking the full payload without its X-Config-Version ever advancing.

These tests pin the discrimination, because a detector that cannot tell a
poisoned client from a healthy one the morning after a publish is worse
than none.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services import config_stall
from tests.conftest import _insert_user, _jwt_token


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _stalls(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM config_stalls")]
    finally:
        conn.close()


def _age(db_path: str, hours: int) -> None:
    """Backdate first_seen so the elapsed-span threshold is met."""
    then = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE config_stalls SET first_seen_at = ?", (then,))
    conn.commit()
    conn.close()


def _fetch(client, uid, *, name="tiers", version, build="803"):
    return client.get(
        f"/v1/config/{name}",
        headers={"Authorization": f"Bearer {_jwt_token(uid)}",
                 "X-App-ID": "shouldersurf",
                 "X-App-Build": build,
                 "X-App-Version": "1.0",
                 "X-Config-Version": str(version)},
    )


# --- what the detector sees -------------------------------------------


def test_repeated_full_payloads_at_one_version_are_recorded(client, app_env):
    db_path = _db(app_env)
    _insert_user(db_path, "stuck-user")
    for _ in range(3):
        assert _fetch(client, "stuck-user", version=1).status_code == 200

    rows = _stalls(db_path)
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 3
    assert rows[0]["client_version"] == 1
    assert rows[0]["app_build"] == "803"


def test_advancing_client_clears_its_stall(client, app_env):
    """The healthy case: one full payload after a publish, then the client
    comes back holding the new version and is never mentioned again."""
    db_path = _db(app_env)
    _insert_user(db_path, "healthy-user")
    _fetch(client, "healthy-user", version=1)
    assert len(_stalls(db_path)) == 1

    # Comes back holding something current — proof it decoded and cached.
    from app.routers.config import load_remote_configs
    server_version = load_remote_configs()["tiers"]["version"]
    r = _fetch(client, "healthy-user", version=server_version)
    assert r.json()["changed"] is False
    assert _stalls(db_path) == []


def test_partial_advance_retires_the_old_row(client, app_env):
    """A client that moves from v1 to v2 decoded v1 fine; only the newer
    stall should survive, otherwise every publish leaves litter that ages
    into a false alert."""
    db_path = _db(app_env)
    _insert_user(db_path, "moving-user")
    _fetch(client, "moving-user", version=1)
    _fetch(client, "moving-user", version=2)

    rows = _stalls(db_path)
    assert len(rows) == 1
    assert rows[0]["client_version"] == 2


def test_unauthenticated_fetch_is_not_tracked(client, app_env):
    """Pre-login fetches have no stable identity across launches, so they
    cannot demonstrate a loop."""
    r = client.get("/v1/config/tiers",
                   headers={"X-App-ID": "shouldersurf", "X-Config-Version": "1"})
    assert r.status_code == 200
    assert _stalls(_db(app_env)) == []


def test_config_delivery_survives_bookkeeping_failure(client, app_env, monkeypatch):
    """Config must be served even if detection breaks. It is diagnostics,
    not a gate."""
    _insert_user(_db(app_env), "resilient-user")

    async def _boom(*a, **k):
        raise RuntimeError("bookkeeping is down")

    monkeypatch.setattr(config_stall, "record_full_payload", _boom)
    r = _fetch(client, "resilient-user", version=1)
    assert r.status_code == 200
    assert "version" in r.json()


# --- when it fires ----------------------------------------------------


@pytest.mark.anyio
async def test_alert_needs_both_count_and_elapsed_time(client, app_env):
    """A burst inside one launch is not a loop. Four launches spread over
    hours is."""
    import aiosqlite
    db_path = _db(app_env)
    _insert_user(db_path, "burst-user")
    for _ in range(config_stall.ALERT_MIN_OCCURRENCES):
        _fetch(client, "burst-user", version=1)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Count is met, span is not.
        assert await config_stall.due_alerts(db) == []

    _age(db_path, config_stall.ALERT_MIN_SPAN_HOURS + 1)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        assert len(await config_stall.due_alerts(db)) == 1


@pytest.mark.anyio
async def test_alert_fires_once_and_carries_the_diagnosis(client, app_env):
    import aiosqlite
    db_path = _db(app_env)
    _insert_user(db_path, "loop-user")
    for _ in range(config_stall.ALERT_MIN_OCCURRENCES):
        _fetch(client, "loop-user", version=1)
    _age(db_path, config_stall.ALERT_MIN_SPAN_HOURS + 1)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        assert await config_stall.alert_stalled_clients(db) == 1
        # Already reported: the sweep must not re-alert every hour forever.
        assert await config_stall.alert_stalled_clients(db) == 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT subject, details_json FROM alert_incidents "
        "WHERE category='config_decode_loop'")]
    conn.close()
    assert len(rows) == 1
    assert "803" in rows[0]["details_json"]
    assert "stuck_on_version" in rows[0]["details_json"]


@pytest.mark.anyio
async def test_a_few_fetches_never_alert(client, app_env):
    """Under the occurrence floor, no matter how old."""
    import aiosqlite
    db_path = _db(app_env)
    _insert_user(db_path, "quiet-user")
    _fetch(client, "quiet-user", version=1)
    _age(db_path, 72)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        assert await config_stall.alert_stalled_clients(db) == 0
