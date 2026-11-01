"""Skipping the migration sweep must never cost the self-healing property.

The sweep is slow BECAUSE it is self-healing: 155 statements, 95 idempotent
no-ops and 60 swallowed "duplicate column name", converging the schema on
the truth from any starting state. Measured on prod 2026-09-06 that costs
4.93s cold and 15.09s of a 17.31s boot, and the port is shut for all of it.

Recording what has been applied would replace self-healing with a CLAIM,
and a claim can be wrong: a volume restored from a snapshot taken
mid-migration, a database promoted from elsewhere, a migration edited after
being recorded. The failure would stop being a slow boot and become a
silently wrong schema serving traffic. So the record is gated on a
fingerprint of the schema that is really there, and any mismatch falls back
to the full sweep. These tests exist to prove the fallback still fires.
"""
import asyncio

import aiosqlite
import pytest

from app.database import (
    MIGRATIONS, apply_migrations, migrations_fingerprint, schema_fingerprint,
)


async def _fresh(tmp_path):
    db = await aiosqlite.connect(str(tmp_path / "t.db"))
    from app.database import SCHEMA_SQL
    await db.executescript(SCHEMA_SQL)
    return db


@pytest.mark.asyncio
async def test_first_run_sweeps_and_second_run_skips(tmp_path):
    db = await _fresh(tmp_path)
    first = await apply_migrations(db)
    assert first["path"] == "swept" and first["ran"] > 0
    assert first["failed"] == 0

    second = await apply_migrations(db)
    assert second["path"] == "skipped"
    assert second["ran"] == 0, "the whole point: zero statements on a normal boot"
    await db.close()


@pytest.mark.asyncio
async def test_a_tampered_schema_falls_back_to_the_full_sweep_and_heals(tmp_path):
    """The property that must not be traded away. Drop a column the
    migrations add, and the next boot has to notice and put it back."""
    db = await _fresh(tmp_path)
    await apply_migrations(db)
    assert (await apply_migrations(db))["path"] == "skipped"

    cols = lambda rows: {r[1] for r in rows}
    before = cols(await (await db.execute("PRAGMA table_info(usage_log)")).fetchall())
    assert "metadata" in before

    await db.execute("ALTER TABLE usage_log DROP COLUMN metadata")
    after_drop = cols(await (await db.execute("PRAGMA table_info(usage_log)")).fetchall())
    assert "metadata" not in after_drop, "the sabotage must have landed"

    report = await apply_migrations(db)
    assert report["path"] == "swept", "a changed schema must not be skipped"
    healed = cols(await (await db.execute("PRAGMA table_info(usage_log)")).fetchall())
    assert "metadata" in healed, "the sweep must put back what was dropped"
    await db.close()


@pytest.mark.asyncio
async def test_a_new_migration_forces_one_sweep_then_goes_fast(tmp_path, monkeypatch):
    db = await _fresh(tmp_path)
    await apply_migrations(db)
    assert (await apply_migrations(db))["path"] == "skipped"

    extra = list(MIGRATIONS) + ["ALTER TABLE usage_log ADD COLUMN zzz_probe TEXT"]
    monkeypatch.setattr("app.database.MIGRATIONS", extra)
    report = await apply_migrations(db)
    assert report["path"] == "swept", "an added migration must invalidate the record"
    cols = {r[1] for r in await (await db.execute("PRAGMA table_info(usage_log)")).fetchall()}
    assert "zzz_probe" in cols
    assert (await apply_migrations(db))["path"] == "skipped"
    await db.close()


@pytest.mark.asyncio
async def test_a_lying_record_does_not_win(tmp_path):
    """A record claiming a schema the database does not have must lose to
    the database. This is the snapshot-restore and promoted-database case."""
    db = await _fresh(tmp_path)
    await apply_migrations(db)
    await db.execute(
        "UPDATE schema_state SET schema_fingerprint = 'not-what-is-really-there'")
    report = await apply_migrations(db)
    assert report["path"] == "swept"
    await db.close()


@pytest.mark.asyncio
async def test_no_record_at_all_sweeps(tmp_path):
    db = await _fresh(tmp_path)
    await apply_migrations(db)
    await db.execute("DELETE FROM schema_state")
    assert (await apply_migrations(db))["path"] == "swept"
    await db.close()


@pytest.mark.asyncio
async def test_the_fingerprint_ignores_its_own_bookkeeping_table(tmp_path):
    """schema_state must be excluded, or creating it guarantees a mismatch
    on the first boot after this ships and after every reset."""
    db = await _fresh(tmp_path)
    before = await schema_fingerprint(db)
    await db.execute("CREATE TABLE IF NOT EXISTS schema_state ("
                     "id INTEGER PRIMARY KEY CHECK (id = 1), "
                     "schema_fingerprint TEXT NOT NULL, "
                     "migrations_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL)")
    assert await schema_fingerprint(db) == before
    await db.close()


def test_the_migration_fingerprint_moves_only_on_a_real_change():
    base = migrations_fingerprint(["ALTER TABLE a ADD COLUMN b TEXT"])
    assert base == migrations_fingerprint(["ALTER  TABLE   a ADD COLUMN b TEXT"]), (
        "reformatting is not a new migration")
    assert base != migrations_fingerprint(["ALTER TABLE a ADD COLUMN c TEXT"])
    assert base != migrations_fingerprint(
        ["ALTER TABLE a ADD COLUMN b TEXT", "ALTER TABLE a ADD COLUMN c TEXT"])


@pytest.mark.asyncio
async def test_an_unexpected_migration_error_is_reported_not_swallowed(tmp_path, monkeypatch):
    """The old loop swallowed every exception equally. Real errors are now
    counted and logged; benign 'duplicate column' ones are not."""
    db = await _fresh(tmp_path)
    monkeypatch.setattr("app.database.MIGRATIONS",
                        ["ALTER TABLE table_that_does_not_exist ADD COLUMN x TEXT"])
    report = await apply_migrations(db)
    assert report["failed"] == 1 and report["ran"] == 0
    await db.close()
