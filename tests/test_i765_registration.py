"""I-765 Helper (app_id i765) joins GP the way N-400 did (2026-10-09, the
I-765 lead's ask, PLAN.md section 5 item 1): its own registry entry and
config directory, the anonymous door with the same three caps, the strict
token claim so an N-400 token cannot speak for an I-765 install or the
reverse, no new-account push, and the refusal codes spelled with its own
prefix.

Every assertion here is about i765. N-400's own tests stay byte for byte
the proof that nothing moved for the app that is live on Scott's phone.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.routers.auth import anonymous_sub
from app.services import anonymous_budget, app_budget
from app.services.jwt_service import JWTService
from tests.conftest import _insert_user, chat_request

SECRET = "test-secret-key-that-is-long-enough-for-hs256-validation"
I765 = {"X-App-ID": "i765"}
N400 = {"X-App-ID": "n400"}
SHIPPED = json.load(open("config/remote/i765/budget.json"))
SHIPPED_N400 = json.load(open("config/remote/n400/budget.json"))


def _token(user_id, app=None):
    svc = JWTService(secret=SECRET, algorithm="HS256", access_expire_minutes=60, refresh_expire_days=30)
    return {"Authorization": f"Bearer {svc.create_access_token(user_id, app)}"}


def _anon_user(db, user_id, app):
    _insert_user(db, user_id=user_id, tier="free", monthly_limit=0.35)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET apple_sub = ? WHERE id = ?", (f"anonymous:{uuid.uuid4().hex}", user_id))
    conn.commit(); conn.close()
    return _token(user_id, app)


def _spend(db, user_id, app_id, amount, case_id="an-earlier-application"):
    """Spend on an EARLIER application, so the per-application bucket of the
    current turn (no case_id) stays empty and the install's lifetime is what
    the refusal is about."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO usage_log (id, user_id, app_id, provider, model, estimated_cost_usd, "
        "request_timestamp, status, case_id) VALUES (?,?,?,?,?,?,?, 'success', ?)",
        (str(uuid.uuid4()), user_id, app_id, "anthropic", "m", amount,
         datetime.now(timezone.utc).isoformat(), case_id))
    conn.commit(); conn.close()


def _serve(client, **docs):
    client.app.state.remote_configs = {**client.app.state.remote_configs, **docs}


def _chat(client, headers, app=I765):
    return client.post("/v1/chat", json=chat_request(user_content="Hello"), headers={**headers, **app})


def _state(r):
    return r.json().get("feature_state") or {}


# --- the registry -------------------------------------------------------------


def test_i765_resolves_to_its_own_dir_and_carries_what_the_gate_needs():
    from app.routers import config as cfg
    assert cfg.resolve_app_dir("i765") == "i765"
    assert cfg.resolve_app_dir(" I765 ") == "i765"
    assert cfg.resolve_app_dir("i765helper") == "shouldersurf", "fails open, so the id must be exact"
    entry = cfg.load_apps()["apps"]["i765"]
    assert entry["bundle_id"] == "com.weirtech.i765helper"
    assert entry["label"] == "I-765 Helper"
    assert "tier_overrides" not in entry and "cq" not in entry


def test_i765_budget_is_flat_on_its_own_document_like_n400s():
    from app.routers import config as cfg
    entry = cfg.load_apps()["apps"]["i765"]
    assert entry["budget"]["shape"] == "flat"
    assert entry["budget"]["config_slug"] == "i765/budget"
    assert entry["budget"]["own_account_meter"] is True
    assert entry["budget"]["monthly_cost_limit_usd"] == -1
    # The shipped document is n400's with the name swapped and nothing else.
    assert SHIPPED["anonymous"]["per_application_usd"] == SHIPPED_N400["anonymous"]["per_application_usd"] == 2
    assert SHIPPED["anonymous"]["per_install_lifetime_usd"] == SHIPPED_N400["anonymous"]["per_install_lifetime_usd"] == 10
    assert SHIPPED["anonymous"]["daily_all_installs_usd"] == SHIPPED_N400["anonymous"]["daily_all_installs_usd"] == 50
    assert SHIPPED["device_test"]["user_agent_app_name"] == "I-765 Helper"
    for key in ("application_allowance_used", "install_allowance_used", "daily_capacity_reached"):
        assert set(SHIPPED["anonymous"][key]["text"]) == {"en", "es", "pt"}, key


def test_the_shipped_document_passes_the_audit_and_its_absence_is_refused():
    from app.routers.config import load_apps
    assert app_budget.audit_uncapped_reachable_apps(
        {"i765/budget": SHIPPED, "n400/budget": SHIPPED_N400}, load_apps(), "") == []
    without = {k: v for k, v in SHIPPED.items() if k != "anonymous"}
    found = app_budget.audit_uncapped_reachable_apps(
        {"i765/budget": without, "n400/budget": SHIPPED_N400}, load_apps(), "")
    assert [(v["app_id"], v["reachable_via"]) for v in found] == [("i765", "anonymous")]


def test_the_served_documents_exist_for_every_slug_the_registry_names(client):
    configs = client.app.state.remote_configs
    for slug in ("i765/budget", "i765/entitlements", "i765/interviewer-turn", "i765/policy-matrix"):
        assert isinstance(configs.get(slug), dict), slug
    # And none of them is readable from the phone except entitlements, as n400's.
    for slug, status in (("budget", 404), ("interviewer-turn", 404), ("entitlements", 200)):
        r = client.get(f"/v1/config/{slug}", headers=I765)
        assert r.status_code == status, (slug, r.status_code, r.text[:200])


# --- the anonymous door -------------------------------------------------------


def test_i765_mints_an_install_account_with_an_i765_token_and_no_push(client, tmp_db_path):
    install = str(uuid.uuid4())
    with patch("app.services.new_user_push.schedule") as sched:
        r = client.post("/auth/anonymous", json={"install_id": install}, headers=I765)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_anonymous"] is True
    assert client.app.state.jwt_service.verify_access_token(body["access_token"])["app"] == "i765"
    conn = sqlite3.connect(tmp_db_path)
    sub = conn.execute("SELECT apple_sub FROM users WHERE id=?", (body["user"]["id"],)).fetchone()[0]
    conn.close()
    assert sub == anonymous_sub(install, "i765")
    assert sub != anonymous_sub(install, "n400"), "the same phone is two installs, one per app"
    assert sched.call_count == 0


def test_the_first_turn_through_the_door_is_served(client):
    tok = client.post("/auth/anonymous", json={"install_id": str(uuid.uuid4())}, headers=I765).json()["access_token"]
    r = client.post("/v1/chat", json=chat_request(user_content="Hello"),
                    headers={"Authorization": f"Bearer {tok}", **I765})
    assert r.status_code == 200, r.text
    assert r.json()["text"]
    assert _state(r).get("budget_exhausted") is not True


def test_the_two_apps_tokens_do_not_cross(client, tmp_db_path):
    i = _anon_user(tmp_db_path, "cross-i", "i765")
    n = _anon_user(tmp_db_path, "cross-n", "n400")
    assert _chat(client, i, N400).status_code == 401
    assert _chat(client, i, N400).json()["detail"]["code"] == "token_app_mismatch"
    assert _chat(client, n, I765).status_code == 401
    assert _chat(client, i, I765).status_code == 200
    assert _chat(client, n, N400).status_code == 200


def test_an_unscoped_token_cannot_call_i765(client, tmp_db_path):
    unscoped = _anon_user(tmp_db_path, "unscoped-user", "unscoped")
    r = _chat(client, unscoped, I765)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "token_app_mismatch"


# --- the caps, spelled with the app's own prefix -----------------------------


def test_wire_codes_carry_the_apps_prefix_and_n400s_are_untouched():
    assert anonymous_budget.wire_code("i765", anonymous_budget.CODE_INSTALL) == "i765_install_allowance_used"
    assert anonymous_budget.wire_code("i765", anonymous_budget.CODE_APPLICATION) == "i765_application_allowance_used"
    assert anonymous_budget.wire_code("i765", anonymous_budget.CODE_DAILY) == "i765_daily_capacity_reached"
    for code in (anonymous_budget.CODE_INSTALL, anonymous_budget.CODE_APPLICATION, anonymous_budget.CODE_DAILY):
        assert anonymous_budget.wire_code("n400", code) == code
        assert anonymous_budget.wire_code(None, code) == code


def test_a_spent_i765_install_is_refused_with_its_own_code_and_copy(client, tmp_db_path):
    doc = copy.deepcopy(SHIPPED)
    doc["anonymous"]["install_allowance_used"]["text"]["en"] = "I-765 used."
    _serve(client, **{"i765/budget": doc})
    h = _anon_user(tmp_db_path, "anon-i1", "i765")
    _spend(tmp_db_path, "anon-i1", "i765", 10.0)
    r = _chat(client, h)
    st = _state(r)
    assert r.status_code == 200, r.text
    assert st["budget_exhausted"] is True
    assert st["code"] == "i765_install_allowance_used"
    assert st["app"] == "i765" and st["resets_at"] is None
    assert st["cta"]["text"]["en"] == "I-765 used."
    assert r.json()["text"] == ""


def test_i765_spend_does_not_count_against_an_n400_install_on_the_same_phone(client, tmp_db_path):
    """The caps sum usage_log by (user, app). Two installs, two sums."""
    _serve(client, **{"i765/budget": copy.deepcopy(SHIPPED), "n400/budget": copy.deepcopy(SHIPPED_N400)})
    h = _anon_user(tmp_db_path, "anon-i2", "i765")
    _spend(tmp_db_path, "anon-i2", "n400", 10.0)
    r = _chat(client, h)
    assert r.status_code == 200 and _state(r).get("budget_exhausted") is not True


def test_i765_spend_is_carried_through_a_delete():
    assert "i765" in anonymous_budget.CARRY_APPS and "n400" in anonymous_budget.CARRY_APPS


def test_i765_turns_store_place_columns_like_n400s():
    from app.services.usage_tracker import PLACE_APPS
    assert PLACE_APPS == frozenset({"n400", "i765"})
