"""One transcript per (user, meeting): a repeated upload replaces, never appends.

app/routers/cq_proxy.py stores an upload with INSERT OR REPLACE and a fresh
uuid4() as the primary key. OR REPLACE fires only on a uniqueness conflict,
and a fresh uuid never conflicts, so the REPLACE had never fired once: the
same meeting uploaded twice was two rows. The client's ledger replays an
orphaned upload under the SAME meeting_id, resting on a comment that says GP
is "idempotent per meeting_id (last-write-wins)". The v41 unique index on
(user_id, meeting_id) is what makes that comment true, and these tests are
the proof, run against the same boot path production takes (init_db).

The first test is the defect itself, and it is the one that was made to go
red first: against the pre-v41 schema it reads two rows.
"""
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite
import pytest

from app.database import (
    MIGRATIONS, PyMigration, SCHEMA_SQL, apply_migrations, init_db,
    _dedupe_transcripts_then_unique_index,
)

# The statement in app/routers/cq_proxy.py, verbatim. The route is not
# imported here on purpose: this file proves the SCHEMA, and the end-to-end
# double post in tests/test_capture_transcript_request_passthrough.py drives
# the real route so a drift between the two is caught there.
UPLOAD_SQL = """INSERT OR REPLACE INTO meeting_transcripts
               (id, user_id, meeting_id, transcript, project, project_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)"""

UNIQUE_INDEX = "idx_transcripts_user_meeting"
OLD_INDEX = "idx_transcripts_meeting"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _booted(tmp_path) -> str:
    """The production boot path: SCHEMA_SQL, the migration sweep, commit."""
    path = str(tmp_path / "t.db")
    await init_db(f"sqlite+aiosqlite:///{path}")
    return path


def _rows(path: str, user_id: str, meeting_id: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(
            "SELECT transcript, created_at FROM meeting_transcripts"
            " WHERE user_id = ? AND meeting_id = ? ORDER BY rowid",
            (user_id, meeting_id)).fetchall()
    finally:
        con.close()


def _index_names(path: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
            " AND tbl_name = 'meeting_transcripts'")}
    finally:
        con.close()


@pytest.mark.asyncio
async def test_a_repeated_upload_replaces_its_transcript_instead_of_appending(tmp_path):
    """The exact double insert the route performs: two fresh uuids, one
    (user, meeting). Exactly one row, and it carries the SECOND transcript.
    The content assertion is not decoration: INSERT OR IGNORE would also
    leave one row, holding the first upload, and only the content tells
    those apart."""
    path = await _booted(tmp_path)
    async with aiosqlite.connect(path) as db:
        for text in ("first", "second"):
            await db.execute(UPLOAD_SQL, (
                str(uuid.uuid4()), "u-1", "m-1", text, "Proj", "p-1", _now()))
        await db.commit()

    rows = _rows(path, "u-1", "m-1")
    assert len(rows) == 1, (
        f"a repeated upload appended: {len(rows)} rows for one (user, meeting)")
    assert rows[0][0] == "second", (
        f"the surviving transcript is {rows[0][0]!r}; last write must win")


@pytest.mark.asyncio
async def test_the_migration_is_idempotent_on_a_database_with_rows(tmp_path):
    """Applied again on a database that already has the index and data:
    no error, nothing deleted, both indexes present. A second
    apply_migrations would take the fingerprint fast path and run ZERO
    statements, which would make "apply twice" vacuous, so the record is
    cleared to force the real sweep, and the step is then called directly
    twice more on top of that."""
    path = await _booted(tmp_path)
    async with aiosqlite.connect(path) as db:
        await db.execute(UPLOAD_SQL, (
            str(uuid.uuid4()), "u-1", "m-1", "kept", "Proj", "p-1", _now()))
        await db.commit()

        await db.execute("DELETE FROM schema_state")
        report = await apply_migrations(db)
        assert report["path"] == "swept", "the sweep must really run this time"
        assert report["failed"] == 0, report

        await _dedupe_transcripts_then_unique_index(db)
        await _dedupe_transcripts_then_unique_index(db)
        await db.commit()

    assert [r[0] for r in _rows(path, "u-1", "m-1")] == ["kept"], "nothing may be deleted"
    names = _index_names(path)
    assert UNIQUE_INDEX in names
    assert OLD_INDEX in names, (
        "the meeting_id-only index must survive: reports.py and webhooks.py "
        "look transcripts up by meeting_id alone, which an index led by "
        "user_id cannot serve")


def _seed(db_path: str, rows: list[tuple]) -> None:
    con = sqlite3.connect(db_path)
    try:
        for user_id, meeting_id, transcript, created_at in rows:
            con.execute(
                "INSERT INTO meeting_transcripts"
                " (id, user_id, meeting_id, transcript, project, project_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, meeting_id, transcript, "Proj", "p-1",
                 created_at))
        con.commit()
    finally:
        con.close()


@pytest.mark.asyncio
async def test_existing_duplicates_keep_the_latest_created_at_then_the_highest_rowid(
        tmp_path, monkeypatch, caplog):
    """A database that already holds duplicates when v41 arrives (none on
    prod today, 0 of 265 rows, but an offline device can replay between
    the count and the deploy). The rule is the one the index guarantees
    going forward, applied once to the past: latest created_at wins, tie
    on created_at goes to the highest rowid. And it is logged at WARNING
    with the count, because a non-zero number is a fact to be seen."""
    path = str(tmp_path / "t.db")
    db = await aiosqlite.connect(path)
    await db.executescript(SCHEMA_SQL)

    # The schema exactly as prod had it before v41: every migration but
    # this one, so the duplicates can be seeded at all.
    full = MIGRATIONS
    monkeypatch.setattr("app.database.MIGRATIONS",
                        [m for m in full if not isinstance(m, PyMigration)])
    assert (await apply_migrations(db))["failed"] == 0
    await db.commit()
    assert UNIQUE_INDEX not in _index_names(path), "the pre-v41 schema must have no unique index"

    _seed(path, [
        # m-a: the NEWER created_at is inserted first, so a rule that kept
        # the highest rowid alone would keep the wrong row.
        ("u-1", "m-a", "newer by created_at", "2026-09-10T00:00:00+00:00"),
        ("u-1", "m-a", "older by created_at", "2026-09-01T00:00:00+00:00"),
        # m-b: identical created_at, so the tie-break decides: highest rowid.
        ("u-1", "m-b", "tie, inserted first", "2026-09-05T00:00:00+00:00"),
        ("u-1", "m-b", "tie, inserted second", "2026-09-05T00:00:00+00:00"),
        # m-c: a single row, must be untouched.
        ("u-1", "m-c", "single", "2026-09-05T00:00:00+00:00"),
        # The same meeting id under ANOTHER user is not a duplicate.
        ("u-2", "m-a", "other user", "2026-08-01T00:00:00+00:00"),
    ])
    assert len(_rows(path, "u-1", "m-a")) == 2, "the seed must have landed as duplicates"

    monkeypatch.setattr("app.database.MIGRATIONS", full)
    with caplog.at_level(logging.WARNING, logger="app.database"):
        report = await apply_migrations(db)
    assert report["path"] == "swept" and report["failed"] == 0, report
    await db.commit()
    await db.close()

    survivors = {
        ("u-1", "m-a"): [r[0] for r in _rows(path, "u-1", "m-a")],
        ("u-1", "m-b"): [r[0] for r in _rows(path, "u-1", "m-b")],
        ("u-1", "m-c"): [r[0] for r in _rows(path, "u-1", "m-c")],
        ("u-2", "m-a"): [r[0] for r in _rows(path, "u-2", "m-a")],
    }
    assert survivors == {
        ("u-1", "m-a"): ["newer by created_at"],
        ("u-1", "m-b"): ["tie, inserted second"],
        ("u-1", "m-c"): ["single"],
        ("u-2", "m-a"): ["other user"],
    }
    assert UNIQUE_INDEX in _index_names(path)

    fired = [r for r in caplog.records
             if r.getMessage().startswith("meeting_transcripts_deduped")]
    assert len(fired) == 1, "the dedupe must be audible, exactly once"
    assert fired[0].levelno == logging.WARNING
    assert "meetings=2 rows_deleted=2" in fired[0].getMessage(), fired[0].getMessage()


@pytest.mark.asyncio
async def test_the_dedupe_and_the_index_share_one_transaction(tmp_path):
    """The window the migration closes by construction: nothing can land
    between the DELETE and the CREATE UNIQUE INDEX, because they are one
    transaction. Proved from the other side: a rollback after the step
    undoes BOTH the dedupe and the index. If either had been committed on
    its own, this would show it."""
    path = await _booted(tmp_path)
    async with aiosqlite.connect(path) as db:
        await db.execute(f"DROP INDEX {UNIQUE_INDEX}")
        for text, stamp in (("older", "2026-09-01T00:00:00+00:00"),
                            ("newer", "2026-09-02T00:00:00+00:00")):
            await db.execute(UPLOAD_SQL, (
                str(uuid.uuid4()), "u-1", "m-1", text, "Proj", "p-1", stamp))
        await db.commit()
        assert len(_rows(path, "u-1", "m-1")) == 2

        await _dedupe_transcripts_then_unique_index(db)
        # Which row survives is the previous test's property; this one only
        # cares that a row went and the index came, together.
        inside = await (await db.execute(
            "SELECT COUNT(*) FROM meeting_transcripts WHERE user_id='u-1' AND meeting_id='m-1'"
        )).fetchone()
        assert inside[0] == 1, "one row must be gone inside the transaction"
        inside_idx = await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (UNIQUE_INDEX,)
        )).fetchall()
        assert inside_idx, "the index must exist inside the transaction"

        await db.rollback()

    assert [r[0] for r in _rows(path, "u-1", "m-1")] == ["older", "newer"], (
        "the rollback must restore the deleted row: the DELETE was committed on its own")
    assert UNIQUE_INDEX not in _index_names(path), (
        "the rollback must remove the index: the CREATE was committed on its own")
