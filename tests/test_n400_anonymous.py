"""Anonymous N-400 accounts, the parts that must be live BEFORE the door
opens (Scott, 2026-10-05, option 2): a lifetime allowance per install, a
daily ceiling over every anonymous account, the uncapped-and-reachable audit
watching the anonymous door, the token's app claim, no new-account push per
N-400 install, and N-400's place and case columns on usage_log.

Anonymous N-400 users are inserted directly here because n400 is not in
ANONYMOUS_APPS yet; test_n400_anonymous_door.py covers the mint itself once
it is. Served config is set per test, never read from the shipped file, so a
local overlay cannot make this suite pass on a laptop and fail in CI.
"""
from __future__ import annotations

import copy
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services import anonymous_budget, app_budget
from app.services.jwt_service import JWTService
from tests.conftest import _insert_user, chat_request

SECRET = "test-secret-key-that-is-long-enough-for-hs256-validation"
N400 = {"X-App-ID": "n400"}

ANON_DOC = {
    "per_install_lifetime_usd": 2,
    "daily_all_installs_usd": 50,
    "install_allowance_used": {"kind": "install_allowance_used", "text": {"en": "Used."}},
    "daily_capacity_reached": {"kind": "daily_capacity_reached", "text": {"en": "Tomorrow."}},
}


def _token(user_id, app=None):
    svc = JWTService(secret=SECRET, algorithm="HS256", access_expire_minutes=60, refresh_expire_days=30)
    return svc.create_access_token(user_id, app)


def _anon_user(db, user_id, app="n400"):
    _insert_user(db, user_id=user_id, tier="free", monthly_limit=0.35)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET apple_sub = ? WHERE id = ?",
                 (f"anonymous:{uuid.uuid4().hex}", user_id))
    conn.commit(); conn.close()
    return {"Authorization": f"Bearer {_token(user_id, app)}"}


def _spend(db, user_id, app_id, amount, days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO usage_log (id, user_id, app_id, provider, model, estimated_cost_usd, "
        "request_timestamp, status) VALUES (?,?,?,?,?,?,?, 'success')",
        (str(uuid.uuid4()), user_id, app_id, "anthropic", "m", amount, ts))
    conn.commit(); conn.close()


@contextmanager
def _served(client, anonymous=ANON_DOC):
    prev = client.app.state.remote_configs
    doc = {"version": 1, "monthly_cost_limit_usd": -1,
           "exhausted": {"kind": "budget_exhausted", "text": {"en": "x"}}}
    if anonymous is not None:
        doc["anonymous"] = copy.deepcopy(anonymous)
    client.app.state.remote_configs = {**prev, "n400/budget": doc}
    try:
        yield
    finally:
        client.app.state.remote_configs = prev


def _chat(client, headers, app=N400, **meta):
    body = chat_request(user_content="When did you become a permanent resident?")
    if meta:
        body["metadata"] = {**(body.get("metadata") or {}), **meta}
    return client.post("/v1/chat", json=body, headers={**headers, **app})


def _state(r):
    return (r.json().get("feature_state") or {})


# --- 1. caps -----------------------------------------------------------------

def test_the_shipped_doc_carries_both_dials():
    import json
    doc = json.load(open("config/remote/n400/budget.json"))
    assert doc["anonymous"]["per_install_lifetime_usd"] == 2
    assert doc["anonymous"]["daily_all_installs_usd"] == 50


def test_an_anonymous_n400_install_is_served_under_its_allowance(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "anon-a")
    with _served(client):
        r = _chat(client, h)
    assert r.status_code == 200, r.text
    assert _state(r).get("budget_exhausted") is not True
    assert r.json()["text"]


def test_the_install_cap_is_lifetime_not_monthly(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "anon-b")
    _spend(tmp_db_path, "anon-b", "n400", 2.0, days_ago=60)
    with _served(client):
        r = _chat(client, h)
    st = _state(r)
    assert r.status_code == 200
    assert st["budget_exhausted"] is True and st["code"] == "n400_install_allowance_used"
    assert st["resets_at"] is None and st["app"] == "n400"
    assert st["cta"]["text"]["en"] == "Used."
    assert r.json()["text"] == ""


def test_the_daily_ceiling_counts_every_anonymous_install_and_raises_one_incident(client, tmp_db_path):
    _anon_user(tmp_db_path, "anon-c1"); _anon_user(tmp_db_path, "anon-c2")
    _spend(tmp_db_path, "anon-c1", "n400", 1.9); _spend(tmp_db_path, "anon-c2", "n400", 48.1)
    h = _anon_user(tmp_db_path, "anon-c3")
    with _served(client):
        r = _chat(client, h)
        r2 = _chat(client, h)
    st = _state(r)
    assert st["code"] == "n400_daily_capacity_reached" and st["resets_at"]
    assert st["cta"]["text"]["en"] == "Tomorrow."
    assert _state(r2)["code"] == "n400_daily_capacity_reached"
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute("SELECT COUNT(*) FROM alert_incidents WHERE category='anonymous_daily_cap'").fetchone()[0]
    conn.close()
    assert n == 1


def test_signed_in_spend_does_not_count_toward_the_daily_ceiling(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="harness", tier="automation", monthly_limit=10.0)
    _spend(tmp_db_path, "harness", "n400", 500.0)
    h = _anon_user(tmp_db_path, "anon-d")
    with _served(client):
        r = _chat(client, h)
    assert _state(r).get("budget_exhausted") is not True


def test_yesterdays_anonymous_spend_does_not_count_today(client, tmp_db_path):
    _anon_user(tmp_db_path, "anon-y")
    _spend(tmp_db_path, "anon-y", "n400", 1.0, days_ago=1)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE usage_log SET estimated_cost_usd = 60 WHERE user_id='anon-y'")
    conn.commit(); conn.close()
    h = _anon_user(tmp_db_path, "anon-y2")
    with _served(client):
        assert _state(_chat(client, h)).get("budget_exhausted") is not True


def test_no_anonymous_cap_means_no_allowance_fail_closed(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "anon-e")
    with _served(client, anonymous=None):
        r = _chat(client, h)
    assert r.json()["text"] == ""
    assert _state(r)["credits_total"] == 0


def test_an_anonymous_shouldersurf_account_still_gets_nothing(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "anon-ss", app="shouldersurf")
    with _served(client):
        r = _chat(client, h, app={"X-App-ID": "shouldersurf"})
    assert r.json()["text"] == ""
    assert _state(r)["credits_total"] == 0


# --- 2. the audit sees the anonymous door ---------------------------------------

def _apps():
    from app.routers.config import load_apps
    return load_apps()


def test_the_audit_flags_an_anonymous_app_with_no_cap():
    with patch("app.models.user.ANONYMOUS_APPS", frozenset({"shouldersurf", "n400"})):
        found = app_budget.audit_uncapped_reachable_apps(
            {"n400/budget": {"monthly_cost_limit_usd": -1}}, _apps(), "")
    assert [(v["app_id"], v["reachable_via"]) for v in found] == [("n400", "anonymous")]


def test_the_audit_accepts_an_anonymous_app_whose_anonymous_cap_runs():
    with patch("app.models.user.ANONYMOUS_APPS", frozenset({"shouldersurf", "n400"})):
        found = app_budget.audit_uncapped_reachable_apps(
            {"n400/budget": {"monthly_cost_limit_usd": -1, "anonymous": ANON_DOC}}, _apps(), "")
    assert found == []


def test_the_anonymous_cap_does_not_excuse_the_bundle_door():
    found = app_budget.audit_uncapped_reachable_apps(
        {"n400/budget": {"monthly_cost_limit_usd": -1, "anonymous": ANON_DOC}},
        _apps(), "com.weirtech.n400helper")
    assert [(v["app_id"], v["reachable_via"]) for v in found] == [("n400", "bundle_id")]


def test_the_route_refuses_n400_when_the_anonymous_door_has_no_cap(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="harness2", tier="automation", monthly_limit=10.0)
    h = {"Authorization": f"Bearer {_token('harness2', 'n400')}"}
    with patch("app.models.user.ANONYMOUS_APPS", frozenset({"shouldersurf", "n400"})), \
            _served(client, anonymous=None):
        r = _chat(client, h)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "app_spend_gate_misconfigured"


# --- 3. the token's app claim --------------------------------------------------

def test_a_shouldersurf_token_cannot_call_n400(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="ssu", tier="pro", monthly_limit=5.1)
    r = _chat(client, {"Authorization": f"Bearer {_token('ssu', 'shouldersurf')}"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "token_app_mismatch"


def test_an_unscoped_token_cannot_call_n400(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="uns", tier="pro", monthly_limit=5.1)
    r = _chat(client, {"Authorization": f"Bearer {_token('uns', 'unscoped')}"})
    assert r.status_code == 401


def test_an_n400_token_can_call_n400_and_not_shouldersurf(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="nu", tier="automation", monthly_limit=10.0)
    h = {"Authorization": f"Bearer {_token('nu', 'n400')}"}
    with _served(client):
        assert _chat(client, h).status_code == 200
    assert _chat(client, h, app={"X-App-ID": "shouldersurf"}).status_code == 401


def test_a_non_strict_mismatch_is_logged_and_served(client, tmp_db_path, caplog):
    _insert_user(tmp_db_path, user_id="tru", tier="pro", monthly_limit=5.1)
    h = {"Authorization": f"Bearer {_token('tru', 'shouldersurf')}"}
    with caplog.at_level("WARNING"):
        r = _chat(client, h, app={"X-App-ID": "techrehearsal"})
    assert r.status_code == 200
    assert "token_app_mismatch_observed" in caplog.text


def test_a_legacy_token_with_no_claim_is_still_served(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="leg", tier="automation", monthly_limit=10.0)
    with _served(client):
        assert _chat(client, {"Authorization": f"Bearer {_token('leg')}"}).status_code == 200


def _claim(client, token):
    return client.app.state.jwt_service.verify_access_token(token).get("app")


def test_an_anonymous_mint_carries_its_app(client):
    r = client.post("/auth/anonymous", json={"install_id": str(uuid.uuid4())},
                    headers={"X-App-ID": "shouldersurf"})
    assert _claim(client, r.json()["access_token"]) == "shouldersurf"


def _refresh_row(db, user_id, app_id):
    raw = uuid.uuid4().hex
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at, app_id) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, JWTService.hash_token(raw), "2099-01-01T00:00:00+00:00",
         datetime.now(timezone.utc).isoformat(), app_id))
    conn.commit(); conn.close()
    return raw


def test_a_shouldersurf_session_cannot_be_refreshed_into_n400(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="rs", tier="pro", monthly_limit=5.1)
    raw = _refresh_row(tmp_db_path, "rs", "shouldersurf")
    r = client.post("/auth/refresh", json={"refresh_token": raw}, headers=N400)
    assert r.status_code == 200
    assert _claim(client, r.json()["access_token"]) == "shouldersurf"


def test_a_legacy_session_reaches_n400_only_if_the_account_belongs_to_it(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="lg", tier="pro", monthly_limit=5.1)
    raw = _refresh_row(tmp_db_path, "lg", None)
    r = client.post("/auth/refresh", json={"refresh_token": raw}, headers=N400)
    assert _claim(client, r.json()["access_token"]) == "unscoped"
    # and the rotated session is not attributed to n400, so a second refresh
    # cannot launder it either
    r2 = client.post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}, headers=N400)
    assert _claim(client, r2.json()["access_token"]) == "unscoped"

    _insert_user(tmp_db_path, user_id="lg2", tier="automation", monthly_limit=10.0)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("INSERT INTO user_apps (user_id, app_id, first_seen_at, last_seen_at) "
                 "VALUES ('lg2','n400','x','x')")
    conn.commit(); conn.close()
    raw2 = _refresh_row(tmp_db_path, "lg2", None)
    r3 = client.post("/auth/refresh", json={"refresh_token": raw2}, headers=N400)
    assert _claim(client, r3.json()["access_token"]) == "n400"


# --- 4. push, and the namespaced anonymous sub -----------------------------------

def test_n400_mints_are_not_pushed_and_shouldersurf_mints_are(client):
    with patch("app.routers.auth.ANONYMOUS_APPS", frozenset({"shouldersurf", "n400"})), \
            patch("app.services.new_user_push.schedule") as sched:
        assert client.post("/auth/anonymous", json={"install_id": str(uuid.uuid4())},
                           headers=N400).status_code == 200
        assert sched.call_count == 0
        client.post("/auth/anonymous", json={"install_id": str(uuid.uuid4())},
                    headers={"X-App-ID": "shouldersurf"})
        assert sched.call_count == 1


def test_one_install_id_names_a_different_account_per_app():
    import hashlib
    from app.routers.auth import anonymous_sub
    iid = str(uuid.uuid4())
    # ShoulderSurf keeps the form its existing accounts were minted under.
    assert anonymous_sub(iid) == "anonymous:" + hashlib.sha256(iid.encode()).hexdigest()
    assert anonymous_sub(iid, "n400") != anonymous_sub(iid)
    assert anonymous_sub(iid.upper(), "n400") == anonymous_sub(iid, "n400")


# --- 6. place and case on usage_log ----------------------------------------------

def _last_row(db, user_id):
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute(
            "SELECT * FROM usage_log WHERE user_id=? ORDER BY request_timestamp DESC LIMIT 1",
            (user_id,)).fetchone())
    finally:
        conn.close()


def test_n400_calls_store_state_case_form_and_ip_place(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="pl", tier="automation", monthly_limit=10.0)
    h = {"Authorization": f"Bearer {_token('pl', 'n400')}"}
    case = str(uuid.uuid4()).upper()
    with _served(client), patch("app.services.geoip.lookup",
                                return_value={"country": "US", "region": "Texas", "city": "Austin"}):
        r = _chat(client, h, jurisdiction="US-CA", case_id=case, form_state="CA",
                  form_city="  Fresno ", geo_city="Spoofed")
    assert r.status_code == 200, r.text
    row = _last_row(tmp_db_path, "pl")
    assert row["jurisdiction"] == "US-CA" and row["case_id"] == case.lower()
    assert (row["form_state"], row["form_city"]) == ("CA", "Fresno")
    # the IP's place, never the client's word for it
    assert (row["geo_country"], row["geo_region"], row["geo_city"]) == ("US", "Texas", "Austin")


def test_malformed_values_store_null_and_unknown_is_kept(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="pm", tier="automation", monthly_limit=10.0)
    h = {"Authorization": f"Bearer {_token('pm', 'n400')}"}
    with _served(client):
        _chat(client, h, jurisdiction="California", case_id="not-a-uuid")
        row = _last_row(tmp_db_path, "pm")
        assert row["jurisdiction"] is None and row["case_id"] is None
        _chat(client, h, jurisdiction="US-unknown")
    assert _last_row(tmp_db_path, "pm")["jurisdiction"] == "US-unknown"


def test_other_apps_store_no_place(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="so", tier="pro", monthly_limit=5.1)
    h = {"Authorization": f"Bearer {_token('so', 'shouldersurf')}"}
    _chat(client, h, app={"X-App-ID": "shouldersurf"}, jurisdiction="US-CA",
          case_id=str(uuid.uuid4()))
    row = _last_row(tmp_db_path, "so")
    assert row["jurisdiction"] is None and row["case_id"] is None and row["geo_country"] is None


# --- 7. dashboard ----------------------------------------------------------------

def test_the_place_panel_groups_by_each_source_and_obeys_the_app_filter(client, tmp_db_path, app_env):
    _insert_user(tmp_db_path, user_id="pd", tier="automation", monthly_limit=10.0)
    h = {"Authorization": f"Bearer {_token('pd', 'n400')}"}
    case = str(uuid.uuid4())
    with _served(client), patch("app.services.geoip.lookup",
                                return_value={"country": "US", "region": "Texas", "city": "Austin"}):
        _chat(client, h, jurisdiction="US-TX", case_id=case, form_state="TX", form_city="Austin")
        _chat(client, h, jurisdiction="US-TX", case_id=case)
    key = {"X-Admin-Key": client.app.state.settings.admin_key}
    d = client.get("/webhooks/admin/usage-by-place?days=7", headers=key).json()
    tx = [x for x in d["by_jurisdiction"] if x["jurisdiction"] == "US-TX"][0]
    assert (tx["app_id"], tx["calls"], tx["cases"], tx["users"]) == ("n400", 2, 1, 1)
    assert d["by_form_address"][0]["form_city"] == "Austin"
    assert d["by_ip_location"][0]["geo_region"] == "Texas"
    only_ss = client.get("/webhooks/admin/usage-by-place?days=7&app=shouldersurf", headers=key).json()
    assert only_ss["by_jurisdiction"] == []


# --- query plans: both ceilings run on every anonymous turn ------------------------

def _plan(db, sql, params):
    conn = sqlite3.connect(db)
    try:
        return " | ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
    finally:
        conn.close()


def test_the_lifetime_sum_never_reads_the_table(client, tmp_db_path):
    plan = _plan(tmp_db_path, anonymous_budget.LIFETIME_SPEND_SQL, ("u", "n400"))
    assert "USING COVERING INDEX idx_usage_user_app_date_cost" in plan, plan


def test_the_daily_sum_never_reads_usage_log(client, tmp_db_path):
    plan = _plan(tmp_db_path, anonymous_budget.DAILY_SPEND_SQL, ("n400", "2026-10-05", "anonymous:"))
    # `l` is usage_log's alias in the statement; users is read by primary key.
    assert "SEARCH l USING COVERING INDEX idx_usage_app_date_user_cost" in plan, plan
