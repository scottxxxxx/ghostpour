"""Account deletion endpoint (App Review 5.1.1(v), SS ask 2026-07-25).

POST /v1/account/delete: JWT-authed, idempotent, purges every user-keyed
row plus staged artifact bytes, fires the CQ account_deleted signal, and
revokes the Sign in with Apple token when a fresh authorization code
arrives and the SIWA key is configured.
"""

import datetime
import sqlite3

from app.services.account_deletion import USER_KEYED_TABLES
from tests.conftest import _insert_user, _jwt_token


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]

_NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()


def _seed_rows(db_path: str, user_id: str, staged_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO meeting_transcripts (id, user_id, meeting_id, transcript, created_at) "
        "VALUES (?,?,?,?,?)",
        (f"mt-{user_id}", user_id, "m-1", "hello", _NOW))
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at, revoked) "
        "VALUES (?,?,?,?,?,0)",
        (f"rt-{user_id}", user_id, "hash", _NOW, _NOW))
    conn.execute(
        "INSERT INTO project_prefs (user_id, project_id, key, value, updated_at) "
        "VALUES (?,?,?,?,?)",
        (user_id, "p-1", "gantt_style", "detailed", _NOW))
    conn.execute(
        "INSERT INTO plan_snapshots (id, user_id, template_id, tasks_json, created_at) "
        "VALUES (?,?,?,?,?)",
        (f"ps-{user_id}", user_id, "gantt_detailed", "[]", _NOW))
    conn.execute(
        "INSERT INTO generated_files (id, user_id, name, media_type, size_bytes, "
        "storage_path, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (f"gpf-{user_id}", user_id, "a.xlsx", "application/x", 3,
         staged_path, _NOW, "2999-01-01T00:00:00"))
    conn.commit()
    conn.close()


def _count_rows(db_path: str, user_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    out = {}
    for table in USER_KEYED_TABLES:
        out[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = ?",
            (user_id,)).fetchone()[0]
    out["users"] = conn.execute(
        "SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    conn.close()
    return out


def test_delete_purges_user_and_spares_others(client, app_env, tmp_path):
    db_path = _db(app_env)
    staged = tmp_path / "gone.bin"
    staged.write_bytes(b"abc")
    staged_other = tmp_path / "stays.bin"
    staged_other.write_bytes(b"def")
    _insert_user(db_path, "del-user")
    _insert_user(db_path, "other-user")
    _seed_rows(db_path, "del-user", str(staged))
    _seed_rows(db_path, "other-user", str(staged_other))

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('del-user')}"},
                    json={})
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}

    gone = _count_rows(db_path, "del-user")
    assert all(v == 0 for v in gone.values()), gone
    assert not staged.exists()

    kept = _count_rows(db_path, "other-user")
    assert kept["users"] == 1 and kept["meeting_transcripts"] == 1
    assert staged_other.exists()


def test_second_call_is_idempotent_200(client, app_env):
    _insert_user(_db(app_env), "idem-user")
    headers = {"Authorization": f"Bearer {_jwt_token('idem-user')}"}
    assert client.post("/v1/account/delete", headers=headers).status_code == 200
    r2 = client.post("/v1/account/delete", headers=headers)
    assert r2.status_code == 200
    assert r2.json() == {"status": "deleted"}


def test_bad_and_expired_tokens_401(client, app_env):
    _insert_user(_db(app_env), "keep-user")
    r = client.post("/v1/account/delete",
                    headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    r2 = client.post(
        "/v1/account/delete",
        headers={"Authorization":
                 f"Bearer {_jwt_token('keep-user', secret='wrong-secret-that-is-also-long-enough')}"})
    assert r2.status_code == 401
    # and the user survived both
    conn = sqlite3.connect(_db(app_env))
    assert conn.execute("SELECT COUNT(*) FROM users WHERE id='keep-user'").fetchone()[0] == 1


def test_purge_list_covers_every_user_keyed_table(client, app_env):
    """Schema pin: a migration adding a user-keyed table must add it to
    USER_KEYED_TABLES (or deliberately exempt it here)."""
    conn = sqlite3.connect(_db(app_env))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if "user_id" in cols:
            assert table in USER_KEYED_TABLES, (
                f"{table} carries user_id but is not purged on account delete")
    assert "users" in tables


def test_cq_account_deleted_signal_fires(client, app_env, monkeypatch):
    _insert_user(_db(app_env), "cq-user", tier="pro")
    fired = {}

    async def _capture(user_id, old_tier, new_tier, event_type, occurred_at=None):
        fired.update(user_id=user_id, old_tier=old_tier,
                     new_tier=new_tier, event_type=event_type)

    import app.services.context_quilt as cq
    monkeypatch.setattr(cq, "notify_tier_change", _capture)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('cq-user')}"})
    assert r.status_code == 200
    assert fired == {"user_id": "cq-user", "old_tier": "pro",
                     "new_tier": "deleted", "event_type": "account_deleted"}


def test_revoke_skipped_without_siwa_key(client, app_env, monkeypatch):
    """Code present but no SIWA key configured: data still purges, 200."""
    _insert_user(_db(app_env), "rv-user")
    called = []

    async def _boom(code):
        called.append(code)
        return True

    from app.services import siwa_revocation
    monkeypatch.setattr(siwa_revocation, "revoke_with_authorization_code", _boom)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('rv-user')}"},
                    json={"apple_authorization_code": "c_abc"})
    assert r.status_code == 200
    assert called == []            # unconfigured -> skipped


def test_revoke_runs_when_configured(client, app_env, monkeypatch):
    _insert_user(_db(app_env), "rv2-user")
    called = []

    async def _ok(code):
        called.append(code)
        return True

    from app.services import siwa_revocation
    monkeypatch.setattr(siwa_revocation, "is_configured", lambda: True)
    monkeypatch.setattr(siwa_revocation, "revoke_with_authorization_code", _ok)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('rv2-user')}"},
                    json={"apple_authorization_code": "c_def"})
    assert r.status_code == 200
    assert called == ["c_def"]
