"""Generation DISCOVERY (SS contract 2026-09-05), on the REAL schema.

Every test here builds the database through init_db, so the migration
that adds the discovery columns is exercised, not assumed."""

import json
import os
import tempfile

import pytest

from app.services import generation_turns as gt
from app.services import generated_files as gf


@pytest.fixture(autouse=True)
def _clean_registry():
    gt._IN_FLIGHT.clear()
    yield
    gt._IN_FLIGHT.clear()


async def _real_db(tmp):
    import aiosqlite
    from app.database import init_db
    path = os.path.join(tmp, "t.db")
    await init_db(f"sqlite+aiosqlite:///{path}")
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    return db


@pytest.mark.asyncio
async def test_the_migration_adds_the_discovery_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        cols = [r[1] for r in await (await db.execute("PRAGMA table_info(generations)")).fetchall()]
        assert {"project_id", "meeting_id", "session_id", "question", "acked_at"} <= set(cols)
        await db.close()


@pytest.mark.asyncio
async def test_a_running_row_exists_from_begin_and_finish_keeps_its_context():
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        gt.begin("u1", "gen-a")
        await gt.record_start(db, user_id="u1", app_id="ss", generation_id="gen-a",
                              project_id="proj-1", meeting_id=None, session_id="sess-9",
                              question="make me a budget sheet")
        # running and known to this process: listed with honest progress
        listed = await gt.list_for_scope(db, "u1", project_id="proj-1")
        assert listed[0]["status"] == "running" and listed[0]["question"] == "make me a budget sheet"
        assert "elapsed_seconds" in listed[0]
        # the single GET still 404s a running row (running_info answers first in the route)
        assert await gt.lookup_terminal(db, "u1", "gen-a") is None
        files = [{"file_id": "gpf_x", "name": "b.xlsx", "size_bytes": 10, "url": "/v1/generated-files/gpf_x"}]
        await gt.finish(db, user_id="u1", app_id="ss", generation_id="gen-a",
                        status="done", text="here", generated_files=files)
        e = (await gt.list_for_scope(db, "u1", project_id="proj-1"))[0]
        assert e["status"] == "done" and e["text"] == "here" and e["generated_files"] == files
        assert e["session_id"] == "sess-9" and e["project_id"] == "proj-1" and e["acked_at"] is None
        # done rows live 7 days, not 6 hours
        from datetime import datetime, timezone
        exp = datetime.fromisoformat(e["expires_at"]); now = datetime.now(timezone.utc)
        assert (exp - now).total_seconds() > 6 * 24 * 3600
        # the single GET keeps its exact shape
        assert await gt.lookup_terminal(db, "u1", "gen-a") == {"status": "done", "text": "here", "generated_files": files}
        await db.close()


@pytest.mark.asyncio
async def test_list_is_owner_scoped_newest_first_and_empty_never_404():
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        for i, gid in enumerate(("gen-1", "gen-2")):
            gt.begin("u1", gid)
            await gt.record_start(db, user_id="u1", app_id="ss", generation_id=gid, project_id="p", question=f"q{i}")
            await gt.finish(db, user_id="u1", app_id="ss", generation_id=gid, status="done", text=gid)
        got = await gt.list_for_scope(db, "u1", project_id="p")
        assert [g["generation_id"] for g in got] == ["gen-2", "gen-1"]
        assert await gt.list_for_scope(db, "u2", project_id="p") == []
        assert await gt.list_for_scope(db, "u1", project_id="other") == []
        await db.close()


@pytest.mark.asyncio
async def test_ack_is_idempotent_and_owner_scoped():
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        gt.begin("u1", "gen-a")
        await gt.record_start(db, user_id="u1", app_id="ss", generation_id="gen-a", project_id="p")
        await gt.finish(db, user_id="u1", app_id="ss", generation_id="gen-a", status="done", text="x")
        first = await gt.ack(db, "u1", "gen-a")
        second = await gt.ack(db, "u1", "gen-a")
        assert first["acked_at"] and first["acked_at"] == second["acked_at"]
        assert await gt.ack(db, "u2", "gen-a") is None
        assert (await gt.list_for_scope(db, "u1", project_id="p"))[0]["acked_at"] == first["acked_at"]
        await db.close()


@pytest.mark.asyncio
async def test_a_row_still_running_at_boot_becomes_an_honest_failed_row():
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        gt.begin("u1", "gen-a")
        await gt.record_start(db, user_id="u1", app_id="ss", generation_id="gen-a", project_id="p", question="q")
        gt._IN_FLIGHT.clear()  # the process died
        assert await gt.list_for_scope(db, "u1", project_id="p") == []  # unknown running row: not listed
        assert await gt.sweep_lost_to_restart(db) == 1
        e = (await gt.list_for_scope(db, "u1", project_id="p"))[0]
        assert e["status"] == "failed" and e["error"] == gt.LOST_TO_RESTART
        await db.close()


@pytest.mark.asyncio
async def test_failed_rows_expire_in_a_day_and_running_rows_in_six_hours():
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        from datetime import datetime, timezone
        gt.begin("u1", "gen-f")
        await gt.record_start(db, user_id="u1", app_id="ss", generation_id="gen-f", project_id="p")
        row = await (await db.execute("SELECT expires_at FROM generations WHERE generation_id='gen-f'")).fetchone()
        assert 5 * 3600 < (datetime.fromisoformat(row[0]) - datetime.now(timezone.utc)).total_seconds() <= 6 * 3600
        await gt.finish(db, user_id="u1", app_id="ss", generation_id="gen-f", status="failed", error={"code": "x"})
        row = await (await db.execute("SELECT expires_at FROM generations WHERE generation_id='gen-f'")).fetchone()
        assert 23 * 3600 < (datetime.fromisoformat(row[0]) - datetime.now(timezone.utc)).total_seconds() <= 24 * 3600
        await db.close()


@pytest.mark.asyncio
async def test_done_files_move_to_the_seven_day_clock_and_acked_ones_go_first_at_the_cap(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = await _real_db(tmp)
        monkeypatch.setattr(gf, "STAGING_DIR", __import__("pathlib").Path(tmp) / "staging")
        monkeypatch.setattr(gf, "PER_USER_LIVE_CAP_BYTES", 100)
        a = await gf.stage(db, user_id="u1", app_id="ss", name="a.txt", media_type="text/plain", content=b"x" * 60)
        gt.begin("u1", "gen-a")
        await gt.record_start(db, user_id="u1", app_id="ss", generation_id="gen-a", project_id="p")
        await gt.finish(db, user_id="u1", app_id="ss", generation_id="gen-a", status="done", text="t", generated_files=[a])
        from datetime import datetime, timezone
        row = await (await db.execute("SELECT expires_at FROM generated_files WHERE id=?", (a["file_id"],))).fetchone()
        assert (datetime.fromisoformat(row[0]) - datetime.now(timezone.utc)).total_seconds() > 6 * 24 * 3600
        # unacked: a second 60-byte file would exceed the 100-byte cap and is refused
        assert await gf.stage(db, user_id="u1", app_id="ss", name="b.txt", media_type="text/plain", content=b"y" * 60) is None
        # acked: the old file is purged first and the new one lands
        await gt.ack(db, "u1", "gen-a")
        b = await gf.stage(db, user_id="u1", app_id="ss", name="b.txt", media_type="text/plain", content=b"y" * 60)
        assert b is not None
        assert await gf.fetch(db, a["file_id"], "u1") is None
        await db.close()


def test_the_routes_and_the_call_site_exist():
    src = open("app/routers/generations.py").read()
    assert '@router.get("/generations")' in src and '@router.post("/generations/{generation_id}/ack")' in src
    assert src.index('@router.get("/generations")') < src.index('@router.get("/generations/{generation_id}")')
    chat = open("app/routers/chat.py").read()
    i = chat.index("await generation_turns.record_start(")
    assert 'session_id=body.get_meta("session_id")' in chat[i:i + 600] and "question=_raw_user_content" in chat[i:i + 600]
    assert chat.index("_raw_user_content = body.user_content") < chat.index("# 2.5. Server-side prompt assembly")
    main = open("app/main.py").read()
    assert "sweep_lost_to_restart" in main
