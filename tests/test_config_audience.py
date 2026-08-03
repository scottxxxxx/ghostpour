"""Config test audience (2026-08-03, Scott's ask).

Every served config is a live change to an app we cannot roll back per
user. Two incidents in two days made that concrete: a `people` entry
missing two keys discarded the whole tier catalog on every build, and an
envelope hybrid served v1 arrays under a v2 label. Neither was catchable
before it reached everyone, because there was no way to serve a change to
one account first.

So a named set of accounts resolves configs under `tester/` before the
production chain. The fallback is the important half: a tester variant
overrides only the configs it defines, and everything else resolves
exactly as production does, so a tester is never running a wholly separate
build and a stale tester file cannot silently freeze one account in the
past.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.routers.config import CONFIG_DIR, candidate_slugs
from tests.conftest import _insert_user, _jwt_token

ADMIN = {"X-Admin-Key": "test-admin-key"}
SS = {"X-App-ID": "shouldersurf"}


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _make_tester(db_path: str, user_id: str, active: int = 1) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO config_testers "
        "(user_id, label, active, added_at) VALUES (?,?,?,'2026-08-03T00:00:00Z')",
        (user_id, "scott", active))
    conn.commit()
    conn.close()


def _write_tester_config(name: str, payload: dict) -> Path:
    path = CONFIG_DIR / "tester" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_jwt_token(user_id)}", **SS}


# --- resolution order ------------------------------------------------


def test_tester_candidates_come_first_then_production():
    """Order is the contract: tester wins where defined, production
    everywhere else."""
    assert candidate_slugs("shouldersurf", "tiers", "tester") == [
        "tester/shouldersurf/tiers", "tester/tiers",
        "shouldersurf/tiers", "tiers",
    ]


def test_production_audience_is_unchanged():
    """The default path must be byte-identical to before the feature."""
    assert candidate_slugs("shouldersurf", "tiers") == [
        "shouldersurf/tiers", "tiers"]


# --- serving ---------------------------------------------------------


def test_a_tester_gets_the_tester_variant(client, app_env):
    db_path = _db(app_env)
    _insert_user(db_path, "scott-user")
    _make_tester(db_path, "scott-user")
    _write_tester_config("idle-tips", {"version": 999, "tips": ["TESTER ONLY"]})
    from app.routers.config import load_remote_configs
    client.app.state.remote_configs = load_remote_configs()

    r = client.get("/v1/config/idle-tips", headers=_auth("scott-user"))
    assert r.status_code == 200
    assert r.json()["tips"] == ["TESTER ONLY"]
    assert r.headers["x-config-audience"] == "tester"
    assert r.headers["x-config-resolved"] == "tester/idle-tips"


def test_everyone_else_still_gets_production(client, app_env):
    db_path = _db(app_env)
    _insert_user(db_path, "scott-user")
    _insert_user(db_path, "normal-user")
    _make_tester(db_path, "scott-user")
    _write_tester_config("idle-tips", {"version": 999, "tips": ["TESTER ONLY"]})
    from app.routers.config import load_remote_configs
    client.app.state.remote_configs = load_remote_configs()

    r = client.get("/v1/config/idle-tips", headers=_auth("normal-user"))
    assert r.status_code == 200
    assert r.json().get("tips") != ["TESTER ONLY"]
    assert r.headers["x-config-audience"] == "production"


def test_tester_falls_through_for_configs_with_no_variant(client, app_env):
    """The half that keeps a tester honest: no tester/tiers exists, so a
    tester reads exactly what everyone reads."""
    db_path = _db(app_env)
    _insert_user(db_path, "scott-user")
    _make_tester(db_path, "scott-user")

    r = client.get("/v1/config/tiers", headers=_auth("scott-user"))
    assert r.status_code == 200
    assert r.headers["x-config-audience"] == "tester"
    assert not r.headers["x-config-resolved"].startswith("tester/")


def test_retired_tester_returns_to_production(client, app_env):
    db_path = _db(app_env)
    _insert_user(db_path, "scott-user")
    _make_tester(db_path, "scott-user", active=0)
    _write_tester_config("idle-tips", {"version": 999, "tips": ["TESTER ONLY"]})
    from app.routers.config import load_remote_configs
    client.app.state.remote_configs = load_remote_configs()

    r = client.get("/v1/config/idle-tips", headers=_auth("scott-user"))
    assert r.json().get("tips") != ["TESTER ONLY"]
    assert r.headers["x-config-audience"] == "production"


def test_unauthenticated_fetch_is_production(client, app_env):
    """Fails toward production on purpose. One tester seeing production
    config is a nuisance; the base seeing unverified config is the
    incident this exists to prevent."""
    r = client.get("/v1/config/idle-tips", headers=SS)
    assert r.status_code == 200
    assert r.headers["x-config-audience"] == "production"


def test_a_bad_token_does_not_leak_tester_config(client, app_env):
    r = client.get("/v1/config/idle-tips",
                   headers={"Authorization": "Bearer garbage", **SS})
    assert r.status_code == 200
    assert r.headers["x-config-audience"] == "production"


def test_tester_responses_are_not_proxy_cacheable(client, app_env):
    """The audience comes from the bearer token, so a shared cache keyed on
    URL would hand one audience's payload to the other."""
    db_path = _db(app_env)
    _insert_user(db_path, "scott-user")
    _make_tester(db_path, "scott-user")

    tester = client.get("/v1/config/tiers", headers=_auth("scott-user"))
    prod = client.get("/v1/config/tiers", headers=SS)
    assert tester.headers.get("cache-control") == "no-store"
    assert "max-age" in prod.headers.get("cache-control", "")


# --- the dashboard's two questions -----------------------------------


def test_registry_says_what_we_serve_and_to_whom(client, app_env):
    _write_tester_config("idle-tips", {"version": 999, "tips": ["TESTER ONLY"]})
    from app.routers.config import load_remote_configs
    client.app.state.remote_configs = load_remote_configs()

    r = client.get("/webhooks/admin/config-registry", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    by_name = {c["config"]: c for c in body["configs"]}

    assert "tiers" in by_name and by_name["tiers"]["purpose"]
    assert by_name["idle-tips"]["under_test"] is True, (
        "a config with a tester variant is mid-verification and the "
        "dashboard has to show it")
    assert by_name["tiers"]["under_test"] is False
    assert set(by_name["tiers"]["locales"]) >= {"en", "es", "ja", "fr"}


def test_registry_lists_the_test_accounts(client, app_env):
    _insert_user(_db(app_env), "scott-user")
    _make_tester(_db(app_env), "scott-user")
    r = client.get("/webhooks/admin/config-registry", headers=ADMIN)
    assert any(t["user_id"] == "scott-user" for t in r.json()["testers"])


def test_admin_can_add_and_retire_a_tester(client, app_env):
    _insert_user(_db(app_env), "new-tester")
    up = client.put("/webhooks/admin/config-testers", headers=ADMIN,
                    json={"user_id": "new-tester", "label": "scott"})
    assert up.status_code == 200

    r = client.get("/v1/config/tiers", headers=_auth("new-tester"))
    assert r.headers["x-config-audience"] == "tester"

    client.put("/webhooks/admin/config-testers", headers=ADMIN,
               json={"user_id": "new-tester", "active": False})
    r2 = client.get("/v1/config/tiers", headers=_auth("new-tester"))
    assert r2.headers["x-config-audience"] == "production"


@pytest.fixture(autouse=True)
def _cleanup_tester_tree():
    yield
    import shutil
    shutil.rmtree(CONFIG_DIR / "tester", ignore_errors=True)
