"""A test account passing 100 model calls in an hour is switched off until
Scott clears it (2026-09-29). Driven through the real /v1/chat with the
provider mocked, the usage history seeded straight into usage_log."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import _insert_user, _jwt_token


def _seed_calls(db, user_id, n, minutes_ago=5):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO usage_log (id, user_id, provider, model, input_tokens, output_tokens, "
        "estimated_cost_usd, request_timestamp, response_time_ms, status) "
        "VALUES (?, ?, 'anthropic', 'claude-sonnet-5', 1, 1, 0, ?, 1, 'success')",
        [(str(uuid.uuid4()), user_id, ts) for _ in range(n)])
    conn.commit(); conn.close()


def _active(db, user_id):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def _chat(client, uid):
    return client.post("/v1/chat", headers={"Authorization": f"Bearer {_jwt_token(uid)}"},
                       json={"provider": "auto", "model": "auto", "system_prompt": "s", "user_content": "u"})


def test_the_101st_call_in_an_hour_latches_the_test_account_off(client, tmp_db_path):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    _seed_calls(tmp_db_path, "bot", 100)
    r = _chat(client, "bot")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "automation_capped"
    assert _active(tmp_db_path, "bot") == 0
    # and it stays off: the next call is refused at sign-in, before any count
    assert _chat(client, "bot").status_code == 403


def test_under_the_cap_the_test_account_works(client, tmp_db_path):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    _seed_calls(tmp_db_path, "bot", 99)
    assert _chat(client, "bot").status_code == 200
    assert _active(tmp_db_path, "bot") == 1


def test_calls_older_than_an_hour_do_not_count(client, tmp_db_path):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    _seed_calls(tmp_db_path, "bot", 150, minutes_ago=90)
    assert _chat(client, "bot").status_code == 200


def test_a_real_user_is_never_capped(client, tmp_db_path):
    _insert_user(tmp_db_path, "person", tier="pro", monthly_limit=5.10)
    _seed_calls(tmp_db_path, "person", 150)
    assert _chat(client, "person").status_code == 200
    assert _active(tmp_db_path, "person") == 1
