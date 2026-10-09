"""The anonymous N-400 door itself (Scott, 2026-10-05, option 2): n400 is
in ANONYMOUS_APPS, so POST /auth/anonymous with X-App-ID n400 mints an
install account. What must hold the moment it opens is in
test_n400_anonymous.py; this file is the mint and the first turn through it.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from unittest.mock import patch

from app.routers.auth import anonymous_sub
from app.services import app_budget
from tests.conftest import chat_request

N400 = {"X-App-ID": "n400"}
SHIPPED = json.load(open("config/remote/n400/budget.json"))
# i765 shares the anonymous door (2026-10-09); its own door is audited in
# tests/test_i765_registration.py, and here it only has to be capped.
SHIPPED_I765 = json.load(open("config/remote/i765/budget.json"))


def _mint(client, install=None):
    return client.post("/auth/anonymous", json={"install_id": install or str(uuid.uuid4())},
                       headers=N400)


def _with_shipped_doc(client):
    client.app.state.remote_configs = {**client.app.state.remote_configs,
                                       "n400/budget": copy.deepcopy(SHIPPED)}


def test_n400_mints_an_install_account_with_an_n400_token(client, tmp_db_path):
    install = str(uuid.uuid4())
    with patch("app.services.new_user_push.schedule") as sched:
        r = _mint(client, install)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_anonymous"] is True
    assert client.app.state.jwt_service.verify_access_token(body["access_token"])["app"] == "n400"
    conn = sqlite3.connect(tmp_db_path)
    sub = conn.execute("SELECT apple_sub FROM users WHERE id=?", (body["user"]["id"],)).fetchone()[0]
    conn.close()
    assert sub == anonymous_sub(install, "n400") != anonymous_sub(install)
    assert sched.call_count == 0


def test_the_first_turn_through_the_door_is_served(client):
    _with_shipped_doc(client)
    tok = _mint(client).json()["access_token"]
    r = client.post("/v1/chat", json=chat_request(user_content="Hello"),
                    headers={"Authorization": f"Bearer {tok}", **N400})
    assert r.status_code == 200, r.text
    assert r.json()["text"]
    assert (r.json().get("feature_state") or {}).get("budget_exhausted") is not True


def test_the_shipped_config_passes_the_audit_with_the_door_open():
    from app.routers.config import load_apps
    assert app_budget.audit_uncapped_reachable_apps(
        {"n400/budget": SHIPPED, "i765/budget": SHIPPED_I765}, load_apps(), "") == []


def test_the_door_without_its_cap_is_refused():
    from app.routers.config import load_apps
    doc = {k: v for k, v in SHIPPED.items() if k != "anonymous"}
    found = app_budget.audit_uncapped_reachable_apps(
        {"n400/budget": doc, "i765/budget": SHIPPED_I765}, load_apps(), "")
    assert [(v["app_id"], v["reachable_via"]) for v in found] == [("n400", "anonymous")]
