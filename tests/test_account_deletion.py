"""Account deletion endpoint (App Review 5.1.1(v), SS ask 2026-07-25).

POST /v1/account/delete: JWT-authed, idempotent, purges the deleting app's
rows plus staged artifact bytes, fires the CQ account_deleted signal under
that app's CQ identity, and revokes the Sign in with Apple token when a
fresh authorization code arrives and the SIWA key is configured.

Deletion is scoped to X-App-ID (2026-08-01, TR ask). Accounts are shared
across apps because Apple issues its subject identifier per developer team,
so these tests pin the boundary: deleting from one app must leave the other
app's data, the account row, and Apple's token alone until the LAST app
goes.
"""

import datetime
import sqlite3

from app.services.account_deletion import (
    ACCOUNT_TABLES,
    APP_OWNED_TABLES,
    APP_SCOPED_TABLES,
)
from tests.conftest import _insert_user, _jwt_token


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]

_NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()

SS = {"X-App-ID": "shouldersurf"}
TR = {"X-App-ID": "techrehearsal"}


def _member(db_path: str, user_id: str, *app_ids: str) -> None:
    conn = sqlite3.connect(db_path)
    for app_id in app_ids:
        conn.execute(
            "INSERT OR REPLACE INTO user_apps "
            "(user_id, app_id, first_seen_at, last_seen_at) VALUES (?,?,?,?)",
            (user_id, app_id, _NOW, _NOW))
    conn.commit()
    conn.close()


def _seed_app_rows(db_path: str, user_id: str, app_id: str,
                   staged_path: str) -> None:
    """One row per representative app-scoped table, tagged to `app_id`."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, "
        "created_at, revoked, app_id) VALUES (?,?,?,?,?,0,?)",
        (f"rt-{user_id}-{app_id}", user_id, f"hash-{app_id}", _NOW, _NOW, app_id))
    conn.execute(
        "INSERT INTO usage_log (id, user_id, provider, model, "
        "request_timestamp, status, app_id) VALUES (?,?,?,?,?,?,?)",
        (f"ul-{user_id}-{app_id}", user_id, "anthropic", "m", _NOW, "success",
         app_id))
    conn.execute(
        "INSERT INTO plan_snapshots (id, user_id, template_id, tasks_json, "
        "created_at, app_id) VALUES (?,?,?,?,?,?)",
        (f"ps-{user_id}-{app_id}", user_id, "gantt_detailed", "[]", _NOW, app_id))
    conn.execute(
        "INSERT INTO generated_files (id, user_id, name, media_type, "
        "size_bytes, storage_path, created_at, expires_at, app_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"gpf-{user_id}-{app_id}", user_id, "a.xlsx", "application/x", 3,
         staged_path, _NOW, "2999-01-01T00:00:00", app_id))
    conn.commit()
    conn.close()


def _seed_ss_owned(db_path: str, user_id: str) -> None:
    """Rows in tables whose domain belongs to Shoulder Surf alone."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO meeting_transcripts (id, user_id, meeting_id, transcript, "
        "created_at) VALUES (?,?,?,?,?)",
        (f"mt-{user_id}", user_id, f"m-{user_id}", "hello", _NOW))
    conn.execute(
        "INSERT INTO meeting_reports (id, user_id, meeting_id, report_json, "
        "report_html, created_at) VALUES (?,?,?,?,?,?)",
        (f"mr-{user_id}", user_id, f"m-{user_id}", "{}", "<p></p>", _NOW))
    conn.execute(
        "INSERT INTO project_prefs (user_id, project_id, key, value, "
        "updated_at) VALUES (?,?,?,?,?)",
        (user_id, "p-1", "gantt_style", "detailed", _NOW))
    conn.commit()
    conn.close()


def _seed_account_rows(db_path: str, user_id: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO welcome_email_queue (user_id, tier, is_trial, due_at, "
        "enqueued_at) VALUES (?,?,0,?,?)", (user_id, "pro", _NOW, _NOW))
    conn.commit()
    conn.close()


def _count(db_path: str, table: str, user_id: str,
           app_id: str | None = None) -> int:
    conn = sqlite3.connect(db_path)
    sql = f"SELECT COUNT(*) FROM {table} WHERE user_id = ?"
    params: list = [user_id]
    if app_id is not None:
        sql += " AND app_id = ?"
        params.append(app_id)
    n = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return n


def _users(db_path: str, user_id: str) -> int:
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM users WHERE id = ?",
                     (user_id,)).fetchone()[0]
    conn.close()
    return n


# --- per-app scoping -------------------------------------------------


def test_deleting_one_app_spares_the_other_and_keeps_the_account(
        client, app_env, tmp_path):
    """The reason this endpoint is scoped: one Apple ID is one users row
    across both apps, so a TR delete must not touch SS."""
    db_path = _db(app_env)
    ss_file = tmp_path / "ss.bin"
    ss_file.write_bytes(b"ss")
    tr_file = tmp_path / "tr.bin"
    tr_file.write_bytes(b"tr")

    _insert_user(db_path, "both-user")
    _member(db_path, "both-user", "shouldersurf", "techrehearsal")
    _seed_app_rows(db_path, "both-user", "shouldersurf", str(ss_file))
    _seed_app_rows(db_path, "both-user", "techrehearsal", str(tr_file))
    _seed_ss_owned(db_path, "both-user")
    _seed_account_rows(db_path, "both-user")

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('both-user')}",
                             **TR})
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}

    # TR's rows and staged bytes are gone.
    for table in ("usage_log", "plan_snapshots", "generated_files"):
        assert _count(db_path, table, "both-user", "techrehearsal") == 0, table
    assert not tr_file.exists()

    # SS survives whole: rows, staged bytes, SS-owned domain tables.
    for table in ("usage_log", "plan_snapshots", "generated_files"):
        assert _count(db_path, table, "both-user", "shouldersurf") == 1, table
    assert ss_file.exists()
    assert _count(db_path, "meeting_transcripts", "both-user") == 1
    assert _count(db_path, "project_prefs", "both-user") == 1

    # The account itself, and its account-level rows, survive.
    assert _users(db_path, "both-user") == 1
    assert _count(db_path, "welcome_email_queue", "both-user") == 1
    assert _count(db_path, "user_apps", "both-user") == 1


def test_deleting_the_last_app_removes_the_account(client, app_env, tmp_path):
    db_path = _db(app_env)
    staged = tmp_path / "last.bin"
    staged.write_bytes(b"x")

    _insert_user(db_path, "solo-user")
    _member(db_path, "solo-user", "techrehearsal")
    _seed_app_rows(db_path, "solo-user", "techrehearsal", str(staged))
    _seed_account_rows(db_path, "solo-user")

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('solo-user')}",
                             **TR})
    assert r.status_code == 200
    assert _users(db_path, "solo-user") == 0
    assert not staged.exists()
    for table in ACCOUNT_TABLES:
        assert _count(db_path, table, "solo-user") == 0, table
    assert _count(db_path, "user_apps", "solo-user") == 0


def test_second_app_delete_finishes_the_account(client, app_env, tmp_path):
    """Delete TR, then SS: the account goes on the second call."""
    db_path = _db(app_env)
    f1, f2 = tmp_path / "a.bin", tmp_path / "b.bin"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")

    _insert_user(db_path, "seq-user")
    _member(db_path, "seq-user", "shouldersurf", "techrehearsal")
    _seed_app_rows(db_path, "seq-user", "shouldersurf", str(f1))
    _seed_app_rows(db_path, "seq-user", "techrehearsal", str(f2))
    _seed_ss_owned(db_path, "seq-user")
    headers = {"Authorization": f"Bearer {_jwt_token('seq-user')}"}

    assert client.post("/v1/account/delete",
                       headers={**headers, **TR}).status_code == 200
    assert _users(db_path, "seq-user") == 1

    assert client.post("/v1/account/delete",
                       headers={**headers, **SS}).status_code == 200
    assert _users(db_path, "seq-user") == 0
    assert _count(db_path, "meeting_transcripts", "seq-user") == 0
    assert not f1.exists() and not f2.exists()


def test_app_owned_tables_only_die_with_their_owner(client, app_env):
    """meeting_* and project_prefs are Shoulder Surf domain: a TR delete
    must not reach them even though they carry no app_id."""
    db_path = _db(app_env)
    _insert_user(db_path, "owned-user")
    _member(db_path, "owned-user", "shouldersurf", "techrehearsal")
    _seed_ss_owned(db_path, "owned-user")

    client.post("/v1/account/delete",
                headers={"Authorization": f"Bearer {_jwt_token('owned-user')}",
                         **TR})
    for table in APP_OWNED_TABLES["shouldersurf"]:
        assert _count(db_path, table, "owned-user") == 1, table


def test_unattributed_request_falls_back_to_full_purge(client, app_env):
    """No usable X-App-ID: under-deleting on a deletion request is the
    worse failure, so everything goes."""
    db_path = _db(app_env)
    _insert_user(db_path, "bare-user")
    _member(db_path, "bare-user", "shouldersurf", "techrehearsal")
    _seed_ss_owned(db_path, "bare-user")
    _seed_account_rows(db_path, "bare-user")

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('bare-user')}"})
    assert r.status_code == 200
    assert _users(db_path, "bare-user") == 0
    assert _count(db_path, "meeting_transcripts", "bare-user") == 0
    assert _count(db_path, "user_apps", "bare-user") == 0


def test_scoped_delete_leaves_unattributed_sessions_alone(client, app_env):
    """Sessions predating the app_id column must survive a scoped delete.

    Sweeping them buys nothing: /auth/refresh inner-joins users, so a token
    whose account is gone cannot be exchanged. All it does is sign the
    person out of the app they did not delete, and today nearly every live
    session is unattributed.
    """
    db_path = _db(app_env)
    _insert_user(db_path, "sess-user")
    _member(db_path, "sess-user", "shouldersurf", "techrehearsal")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, "
        "created_at, revoked, app_id) VALUES (?,?,?,?,?,0,NULL)",
        ("rt-legacy", "sess-user", "legacy-hash", _NOW, _NOW))
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, "
        "created_at, revoked, app_id) VALUES (?,?,?,?,?,0,'techrehearsal')",
        ("rt-tr", "sess-user", "tr-hash", _NOW, _NOW))
    conn.commit()
    conn.close()

    client.post("/v1/account/delete",
                headers={"Authorization": f"Bearer {_jwt_token('sess-user')}",
                         **TR})
    # TR's own session is revoked; the untagged one survives for SS.
    assert _count(db_path, "refresh_tokens", "sess-user", "techrehearsal") == 0
    assert _count(db_path, "refresh_tokens", "sess-user") == 1


def test_full_purge_still_takes_every_session(client, app_env):
    """The account is actually gone, so nothing is left holding a session."""
    db_path = _db(app_env)
    _insert_user(db_path, "wipe-user")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, "
        "created_at, revoked, app_id) VALUES (?,?,?,?,?,0,NULL)",
        ("rt-wipe", "wipe-user", "wipe-hash", _NOW, _NOW))
    conn.commit()
    conn.close()

    client.post("/v1/account/delete",
                headers={"Authorization": f"Bearer {_jwt_token('wipe-user')}"})
    assert _count(db_path, "refresh_tokens", "wipe-user") == 0
    assert _users(db_path, "wipe-user") == 0


def test_other_users_are_never_touched(client, app_env, tmp_path):
    db_path = _db(app_env)
    mine = tmp_path / "mine.bin"
    mine.write_bytes(b"m")
    theirs = tmp_path / "theirs.bin"
    theirs.write_bytes(b"t")

    for uid, path in (("del-user", mine), ("other-user", theirs)):
        _insert_user(db_path, uid)
        _member(db_path, uid, "techrehearsal")
        _seed_app_rows(db_path, uid, "techrehearsal", str(path))

    client.post("/v1/account/delete",
                headers={"Authorization": f"Bearer {_jwt_token('del-user')}",
                         **TR})
    assert _users(db_path, "other-user") == 1
    assert _count(db_path, "usage_log", "other-user") == 1
    assert theirs.exists()
    assert not mine.exists()


# --- contract: idempotency, auth, failure shape ----------------------


def test_second_call_is_idempotent_200(client, app_env):
    _insert_user(_db(app_env), "idem-user")
    _member(_db(app_env), "idem-user", "techrehearsal")
    headers = {"Authorization": f"Bearer {_jwt_token('idem-user')}", **TR}
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
    assert _users(_db(app_env), "keep-user") == 1


def test_purge_failure_returns_distinguishable_body(client, app_env, monkeypatch):
    """TR asked for something better than a bare 500: the app has to be
    able to tell "we could not delete" from a proxy blip."""
    _insert_user(_db(app_env), "fail-user")
    _member(_db(app_env), "fail-user", "techrehearsal")

    async def _boom(db, user_id, app_id=None):
        raise RuntimeError("disk on fire")

    import app.routers.account as account_router
    monkeypatch.setattr(account_router, "delete_user_data", _boom)

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('fail-user')}",
                             **TR})
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "deletion_failed"
    # and the account survived the failed attempt
    assert _users(_db(app_env), "fail-user") == 1


def test_response_body_does_not_leak_cross_app_membership(client, app_env):
    """Identical body whether or not the account survived — otherwise one
    app's operator learns the user is on another of our apps."""
    db_path = _db(app_env)
    _insert_user(db_path, "multi-user")
    _member(db_path, "multi-user", "shouldersurf", "techrehearsal")
    _insert_user(db_path, "single-user")
    _member(db_path, "single-user", "techrehearsal")

    a = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('multi-user')}",
                             **TR})
    b = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('single-user')}",
                             **TR})
    assert a.json() == b.json() == {"status": "deleted"}


# --- schema pin ------------------------------------------------------


def test_every_user_keyed_table_is_classified(client, app_env):
    """A migration adding a user-keyed table must classify it as app-scoped,
    app-owned, or account-level (or be deliberately exempted here)."""
    conn = sqlite3.connect(_db(app_env))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    classified = set(APP_SCOPED_TABLES) | set(ACCOUNT_TABLES) | {
        t for ts in APP_OWNED_TABLES.values() for t in ts}
    # user_apps is the ledger the purge scopes against, handled explicitly.
    exempt = {"user_apps"}

    for table in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if "user_id" in cols and table not in exempt:
            assert table in classified, (
                f"{table} carries user_id but is not classified in "
                "account_deletion.py")
    assert "users" in tables


def test_app_scoped_tables_all_have_app_id(client, app_env):
    """An app-scoped table without an app_id column would silently delete
    nothing on a scoped purge."""
    conn = sqlite3.connect(_db(app_env))
    for table in APP_SCOPED_TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert "app_id" in cols, f"{table} is app-scoped but has no app_id"


# --- CQ signal and SIWA revocation -----------------------------------


def test_cq_account_deleted_signal_carries_the_app(client, app_env, monkeypatch):
    _insert_user(_db(app_env), "cq-user", tier="pro")
    _member(_db(app_env), "cq-user", "techrehearsal")
    fired = {}

    async def _capture(user_id, old_tier, new_tier, event_type,
                       occurred_at=None, offer_id=None, app_id=None):
        fired.update(user_id=user_id, old_tier=old_tier, new_tier=new_tier,
                     event_type=event_type, app_id=app_id)

    import app.services.context_quilt as cq
    monkeypatch.setattr(cq, "notify_tier_change", _capture)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('cq-user')}",
                             **TR})
    assert r.status_code == 200
    assert fired == {"user_id": "cq-user", "old_tier": "pro",
                     "new_tier": "deleted", "event_type": "account_deleted",
                     "app_id": "techrehearsal"}


def test_revoke_skipped_while_another_app_still_uses_the_account(
        client, app_env, monkeypatch):
    """Revoking Apple's token would break the surviving app's sign-in."""
    _insert_user(_db(app_env), "keep-siwa")
    _member(_db(app_env), "keep-siwa", "shouldersurf", "techrehearsal")
    called = []

    async def _rv(code):
        called.append(code)
        return True

    from app.services import siwa_revocation
    monkeypatch.setattr(siwa_revocation, "is_configured", lambda: True)
    monkeypatch.setattr(siwa_revocation, "revoke_with_authorization_code", _rv)

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('keep-siwa')}",
                             **TR},
                    json={"apple_authorization_code": "c_abc"})
    assert r.status_code == 200
    assert called == []


def test_revoke_runs_on_last_app_delete(client, app_env, monkeypatch):
    _insert_user(_db(app_env), "rv2-user")
    _member(_db(app_env), "rv2-user", "techrehearsal")
    called = []

    async def _ok(code):
        called.append(code)
        return True

    from app.services import siwa_revocation
    monkeypatch.setattr(siwa_revocation, "is_configured", lambda: True)
    monkeypatch.setattr(siwa_revocation, "revoke_with_authorization_code", _ok)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('rv2-user')}",
                             **TR},
                    json={"apple_authorization_code": "c_def"})
    assert r.status_code == 200
    assert called == ["c_def"]


def test_revoke_skipped_without_siwa_key(client, app_env, monkeypatch):
    """Code present but no SIWA key configured: data still purges, 200."""
    _insert_user(_db(app_env), "rv-user")
    _member(_db(app_env), "rv-user", "techrehearsal")
    called = []

    async def _boom(code):
        called.append(code)
        return True

    from app.services import siwa_revocation
    monkeypatch.setattr(siwa_revocation, "revoke_with_authorization_code", _boom)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('rv-user')}",
                             **TR},
                    json={"apple_authorization_code": "c_abc"})
    assert r.status_code == 200
    assert called == []


# --- dry-run window ---------------------------------------------------


def test_dryrun_window_returns_200_and_purges_nothing(client, app_env, monkeypatch):
    """App Review recording window (SS ask 2026-07-25): production 200
    shape, zero side effects, SS/headerless scope only."""
    import datetime as dt

    from app.config import get_settings
    _insert_user(_db(app_env), "dry-user", tier="pro")
    _member(_db(app_env), "dry-user", "shouldersurf")
    until = (dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(hours=1)).isoformat()
    monkeypatch.setattr(get_settings(), "account_delete_dryrun_until", until)

    fired = []

    async def _capture(*a, **k):
        fired.append(a)

    import app.services.context_quilt as cq
    monkeypatch.setattr(cq, "notify_tier_change", _capture)
    revoked = []

    async def _rv(code):
        revoked.append(code)
        return True

    from app.services import siwa_revocation
    monkeypatch.setattr(siwa_revocation, "is_configured", lambda: True)
    monkeypatch.setattr(siwa_revocation, "revoke_with_authorization_code", _rv)

    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('dry-user')}",
                             **SS},
                    json={"apple_authorization_code": "c_live"})
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}
    assert _users(_db(app_env), "dry-user") == 1
    assert fired == [] and revoked == []


def test_dryrun_expired_or_malformed_deletes_for_real(client, app_env, monkeypatch):
    import datetime as dt

    from app.config import get_settings
    for uid, until in (("exp-user",
                        (dt.datetime.now(dt.timezone.utc)
                         - dt.timedelta(minutes=1)).isoformat()),
                       ("mal-user", "not-a-timestamp")):
        _insert_user(_db(app_env), uid)
        _member(_db(app_env), uid, "shouldersurf")
        monkeypatch.setattr(get_settings(), "account_delete_dryrun_until", until)
        r = client.post("/v1/account/delete",
                        headers={"Authorization": f"Bearer {_jwt_token(uid)}",
                                 **SS})
        assert r.status_code == 200
        assert _users(_db(app_env), uid) == 0, (uid, until)


def test_dryrun_never_applies_to_other_apps(client, app_env, monkeypatch):
    import datetime as dt

    from app.config import get_settings
    _insert_user(_db(app_env), "tr-user")
    _member(_db(app_env), "tr-user", "techrehearsal")
    until = (dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(hours=1)).isoformat()
    monkeypatch.setattr(get_settings(), "account_delete_dryrun_until", until)
    r = client.post("/v1/account/delete",
                    headers={"Authorization": f"Bearer {_jwt_token('tr-user')}",
                             **TR})
    assert r.status_code == 200
    assert _users(_db(app_env), "tr-user") == 0
