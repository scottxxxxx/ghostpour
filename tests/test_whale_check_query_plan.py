"""The whale check must never read the usage_log table, and must never make a
turn wait.

Prod, 2026-09-22 00:29Z, the N-400 auditor's cold turn: the model finished at
51.78 s, the envelope left at 57.98 s. The six seconds were `check_whale`,
awaited inside `log_usage` before the streamed envelope, fetching the QA
account's 3,731 rows (239 MB of metadata) from the table twice. #1018 had
cured the budget gate's copy of this a day earlier; this statement lacks
`app_id`, so that index was no prefix match for it.

These tests plan the REAL statements against the migrated schema, and time
the real `log_usage` against a slow check.
"""
import asyncio
import sqlite3
import time

import pytest

from app.services import cost_alerts

INDEX = "idx_usage_user_date_cost_calltype"


def _plan(db_path: str, sql: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, ("u",)).fetchall()
    finally:
        conn.close()
    return " | ".join(r[-1] for r in rows)


@pytest.mark.parametrize("sql", [cost_alerts.MONTH_COST_SQL, cost_alerts.TOP_CALL_TYPES_SQL],
                         ids=["month_cost", "top_call_types"])
def test_the_whale_statements_never_read_the_table(client, tmp_db_path, sql):
    plan = _plan(tmp_db_path, sql)
    assert f"USING COVERING INDEX {INDEX}" in plan, plan


@pytest.mark.parametrize("sql", [cost_alerts.MONTH_COST_SQL, cost_alerts.TOP_CALL_TYPES_SQL],
                         ids=["month_cost", "top_call_types"])
def test_without_the_index_sqlite_fetches_every_row(client, tmp_db_path, sql):
    # The shape of the defect, so this file fails for the right reason if the
    # index is ever dropped. The budget gate's index does not help: it leads
    # with app_id, which these statements do not filter on.
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(f"DROP INDEX {INDEX}")
    conn.commit(); conn.close()
    plan = _plan(tmp_db_path, sql)
    assert "COVERING" not in plan and "idx_usage_user_date" in plan, plan


def test_the_index_holds_every_column_both_statements_touch(client, tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    cols = [r[2] for r in conn.execute(f"PRAGMA index_info({INDEX})")]
    conn.close()
    assert cols == ["user_id", "request_timestamp", "estimated_cost_usd", "call_type"]
    for col in cols[:3]:
        assert col in cost_alerts.MONTH_COST_SQL
    for col in cols:
        assert col in cost_alerts.TOP_CALL_TYPES_SQL


@pytest.mark.asyncio
async def test_log_usage_returns_before_the_whale_check_runs(client, monkeypatch):
    """A slow check must not hold the turn: log_usage returns at once and the
    check still runs, on its own connection, afterwards."""
    import aiosqlite
    from app import database
    from app.models.chat import ChatRequest, ChatResponse
    usage_tracker = client.app.state.usage_tracker

    started = asyncio.Event()
    finished = asyncio.Event()
    seen = {}

    async def slow_check(db, user_id):
        seen["user_id"] = user_id
        seen["db_is_open"] = db is not None
        # aiosqlite's connection is a Thread; non-daemon it outlives a closed
        # loop and hangs the interpreter at exit (CI, 2026-09-22, twice).
        seen["daemon"] = getattr(db, "daemon", None)
        started.set()
        await asyncio.sleep(0.4)
        finished.set()

    monkeypatch.setattr(cost_alerts, "check_whale", slow_check)
    req = ChatRequest(provider="anthropic", model="claude-sonnet-5", user_content="hi",
                      metadata={"call_type": "n400_interviewer_turn"})
    resp = ChatResponse(text="{}", input_tokens=10, output_tokens=5, model="claude-sonnet-5",
                        provider="anthropic", usage={"input_tokens": 10, "output_tokens": 5},
                        cost={"total_cost": 0.01})   # a costed request is what schedules the check
    async with aiosqlite.connect(database._db_path) as db:
        db.row_factory = aiosqlite.Row
        t = time.perf_counter()
        await usage_tracker.log_usage(db, "whale-user", req, resp, 100, app_id="n400")
        took = time.perf_counter() - t
    assert took < 0.25, f"log_usage waited {took:.2f}s for the whale check"
    assert not finished.is_set(), "the check finished inside log_usage: it was awaited inline"
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(finished.wait(), 2)
    assert seen == {"user_id": "whale-user", "db_is_open": True, "daemon": True}


@pytest.mark.asyncio
async def test_drain_waits_then_cancels_what_is_still_running(monkeypatch):
    async def stuck(db, user_id):
        await asyncio.sleep(30)
    monkeypatch.setattr(cost_alerts, "check_whale", stuck)
    t = cost_alerts.check_whale_later("u")
    await asyncio.sleep(0)
    assert not t.done()
    assert await cost_alerts.drain(timeout=0.2) == 1
    assert t.done() and t.cancelled()
    assert await cost_alerts.drain(timeout=0.2) == 0


def test_the_app_drains_the_checks_at_shutdown():
    src = open("app/main.py").read()
    y = src.index("yield", src.index("async def lifespan("))
    assert "await cost_alerts.drain()" in src[y:], "shutdown must drain the whale checks"


def test_drain_ignores_tasks_that_belong_to_other_loops(monkeypatch):
    """The registry outlives event loops (a test runner makes many). A task
    from a closed loop is dropped, one from another live loop is left alone,
    and neither is gathered: gathering across loops raises."""
    async def stuck(db, user_id):
        await asyncio.sleep(30)
    monkeypatch.setattr(cost_alerts, "check_whale", stuck)

    async def schedule():
        return cost_alerts.check_whale_later("other")
    dead_loop = asyncio.new_event_loop()
    t_dead = dead_loop.run_until_complete(schedule())
    dead_loop.close()                       # its task can never run again
    assert t_dead in cost_alerts._LIVE

    async def drain_here():
        mine = cost_alerts.check_whale_later("mine")
        await asyncio.sleep(0)
        n = await cost_alerts.drain(timeout=0.2)
        return mine, n
    mine, n = asyncio.run(drain_here())
    assert n == 1 and mine.cancelled()
    assert t_dead not in cost_alerts._LIVE, "a task on a closed loop is dropped, not gathered"
