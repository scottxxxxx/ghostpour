"""Scott's on-device N-400 test (2026-10-05): with n400/budget's
device_test.only_device on, the N-400 lane answers only the phone app's
User-Agent, and the test account's hourly latch is raised for the run."""
from __future__ import annotations

import copy
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import _insert_user, _jwt_token

PHONE = "N-400%20Helper/114 CFNetwork/3860.700.2 Darwin/25.6.0"
HARNESS = "Python-urllib/3.14"


@pytest.fixture
def device_test(client):
    """Turn the switch on in the served config for one test, then restore it."""
    configs = client.app.state.remote_configs
    saved = copy.deepcopy(configs.get("n400/budget"))
    doc = copy.deepcopy(saved) if isinstance(saved, dict) else {}
    doc["device_test"] = {"only_device": True, "user_agent_app_name": "N-400 Helper", "hourly_cap": 400}
    configs["n400/budget"] = doc
    yield
    configs["n400/budget"] = saved


def _seed_calls(db, user_id, n):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO usage_log (id, user_id, provider, model, input_tokens, output_tokens, "
        "estimated_cost_usd, request_timestamp, response_time_ms, status) "
        "VALUES (?, ?, 'anthropic', 'claude-sonnet-5', 1, 1, 0, ?, 1, 'success')",
        [(str(uuid.uuid4()), user_id, ts) for _ in range(n)])
    conn.commit(); conn.close()


def _chat(client, uid, ua, app="n400"):
    return client.post("/v1/chat", headers={
        "Authorization": f"Bearer {_jwt_token(uid)}", "X-App-ID": app, "User-Agent": ua},
        json={"provider": "auto", "model": "auto", "system_prompt": "s", "user_content": "u"})


def test_ships_off(client):
    assert client.app.state.remote_configs["n400/budget"]["device_test"]["only_device"] is False


def test_the_harness_is_refused_while_on(client, tmp_db_path, device_test):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    r = _chat(client, "bot", HARNESS)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "n400_device_test_only"


def test_the_phone_is_served_while_on(client, tmp_db_path, device_test):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    assert _chat(client, "bot", PHONE).status_code == 200


def test_other_apps_are_untouched_while_on(client, tmp_db_path, device_test):
    _insert_user(tmp_db_path, "person", tier="pro", monthly_limit=5.10)
    assert _chat(client, "person", HARNESS, app="shouldersurf").status_code == 200


def test_the_latch_is_raised_for_the_run(client, tmp_db_path, device_test):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    _seed_calls(tmp_db_path, "bot", 150)
    assert _chat(client, "bot", PHONE).status_code == 200


def test_the_raised_latch_still_trips(client, tmp_db_path, device_test):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    _seed_calls(tmp_db_path, "bot", 400)
    r = _chat(client, "bot", PHONE)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "automation_capped"


def test_off_the_harness_is_served_and_the_latch_is_100(client, tmp_db_path):
    _insert_user(tmp_db_path, "bot", tier="automation", monthly_limit=10.0)
    assert _chat(client, "bot", HARNESS).status_code == 200
    _seed_calls(tmp_db_path, "bot", 100)
    assert _chat(client, "bot", PHONE).json()["detail"]["code"] == "automation_capped"
