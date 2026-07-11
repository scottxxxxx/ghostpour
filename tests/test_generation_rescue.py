"""Generation rescue (phase 2, handoff Part 4): in-flight registry,
terminal records, honest-progress 409/running bodies, rescue endpoint,
idempotent replay semantics, shared-clock purge.
"""

import pytest

from app.services import generation_turns as gt

_DDL = """CREATE TABLE generations (
    generation_id TEXT NOT NULL, user_id TEXT NOT NULL, app_id TEXT,
    status TEXT NOT NULL, text TEXT, error_json TEXT, files_json TEXT,
    started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, PRIMARY KEY (generation_id, user_id))"""


@pytest.fixture(autouse=True)
def _clean_registry():
    gt._IN_FLIGHT.clear()
    yield
    gt._IN_FLIGHT.clear()


async def _db():
    import aiosqlite
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_DDL)
    return db


# --- registry + honest progress ---

def test_begin_is_exclusive_and_running_info_is_honest():
    assert gt.begin("u1", "gen-a", expected_seconds=150) is True
    assert gt.begin("u1", "gen-a") is False          # same id: 409 material
    assert gt.begin("u2", "gen-a") is True           # other user: independent
    info = gt.running_info("u1", "gen-a")
    assert info["status"] == "running"
    assert info["expected_seconds"] == 150
    assert info["elapsed_seconds"] >= 0               # TRUE elapsed, not zero-based client guess
    assert info["poll_after_seconds"] == gt.POLL_AFTER_SECONDS
    assert "started_at" in info
    assert gt.running_info("u1", "gen-missing") is None


def test_abandon_clears_without_terminal_row():
    gt.begin("u1", "gen-a")
    gt.abandon("u1", "gen-a")
    assert gt.running_info("u1", "gen-a") is None


# --- terminal records ---

@pytest.mark.asyncio
async def test_finish_done_and_lookup():
    db = await _db()
    gt.begin("u1", "gen-a")
    files = [{"file_id": "gpf_x", "name": "t.xlsx", "sha256": "aa", "url": "/v1/generated-files/gpf_x"}]
    await gt.finish(db, user_id="u1", app_id="ss", generation_id="gen-a",
                    status="done", text="here you go", generated_files=files)
    assert gt.running_info("u1", "gen-a") is None    # registry cleared
    out = await gt.lookup_terminal(db, "u1", "gen-a")
    assert out == {"status": "done", "text": "here you go", "generated_files": files}
    # owner-scoped: another user sees nothing
    assert await gt.lookup_terminal(db, "u2", "gen-a") is None
    await db.close()


@pytest.mark.asyncio
async def test_finish_failed_and_expiry_and_purge():
    db = await _db()
    await gt.finish(db, user_id="u1", app_id="ss", generation_id="gen-f",
                    status="failed", error={"code": "provider_error"})
    out = await gt.lookup_terminal(db, "u1", "gen-f")
    assert out["status"] == "failed" and out["error"]["code"] == "provider_error"
    # force-expire: lookup excludes, purge deletes
    await db.execute("UPDATE generations SET expires_at = '2000-01-01T00:00:00+00:00'")
    await db.commit()
    assert await gt.lookup_terminal(db, "u1", "gen-f") is None
    assert await gt.purge_expired(db) == 1
    await db.close()


# --- rescue endpoint ---

def test_endpoint_running_done_and_uniform_404(client, pro_user, tmp_db_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    h = pro_user["headers"]
    uid = pro_user.get("user_id")
    if uid is None:
        con = sqlite3.connect(tmp_db_path)
        uid = con.execute("SELECT id FROM users WHERE email LIKE 'test-pro-user%'").fetchone()[0]
        con.close()

    # running: registry entry -> honest-progress body
    gt.begin(uid, "gen-run", expected_seconds=150)
    r = client.get("/v1/generations/gen-run", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["poll_after_seconds"] == gt.POLL_AFTER_SECONDS
    assert body["expected_seconds"] == 150
    assert "no-store" in r.headers.get("cache-control", "")
    gt.abandon(uid, "gen-run")

    # done: terminal row -> whole turn
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    con = sqlite3.connect(tmp_db_path)
    con.execute(
        "INSERT INTO generations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("gen-done", uid, "shouldersurf", "done", "answer text", None,
         '[{"file_id": "gpf_1", "name": "t.xlsx"}]', future, future, future))
    con.commit(); con.close()
    r = client.get("/v1/generations/gen-done", headers=h)
    assert r.status_code == 200
    assert r.json() == {"status": "done", "text": "answer text",
                        "generated_files": [{"file_id": "gpf_1", "name": "t.xlsx"}]}

    # uniform 404: absent id, and no auth is its own failure
    assert client.get("/v1/generations/gen-nope", headers=h).status_code == 404
    assert client.get("/v1/generations/gen-done").status_code in (401, 403, 422)
