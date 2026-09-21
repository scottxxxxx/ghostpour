"""The flat app budget's spend query must be answerable from an index alone.

Prod, 2026-09-21: an N-400 turn spent about seven seconds inside GhostPour
before its request reached the provider. Blamed first on Anthropic's queue,
then on a cold prompt cache; `usage_log.ttft_ms` (1,240 ms) said neither. The
budget gate sums a user's spend this month on EVERY turn, the only usable
index was (user_id, request_timestamp), and SQLite fetched each of the
account's 3,710 rows from the table to read two more columns. Warm, nobody
could see it. After a few idle minutes it was the whole wait.

These tests plan the REAL statement (`app_budget.MONTH_SPEND_SQL`), so an edit
that stops it using the index fails here and not on a cold disk in production.
"""
import json
import logging
import sqlite3

from app.services import app_budget

INDEX = "idx_usage_user_app_date_cost"


def _plan(db_path: str, sql: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, ("u", "n400", "2026-09-01")).fetchall()
    finally:
        conn.close()
    return " | ".join(r[-1] for r in rows)


def test_the_spend_query_never_reads_the_table(client, tmp_db_path):
    plan = _plan(tmp_db_path, app_budget.MONTH_SPEND_SQL)
    assert f"USING COVERING INDEX {INDEX}" in plan, plan


def test_the_old_index_alone_was_not_enough(client, tmp_db_path):
    # The shape of the defect, so this file fails for the right reason if the
    # index is ever dropped: with only (user_id, request_timestamp) SQLite
    # finds the rows by index and then FETCHES EACH ONE.
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(f"DROP INDEX {INDEX}")
    conn.commit(); conn.close()
    plan = _plan(tmp_db_path, app_budget.MONTH_SPEND_SQL)
    assert "COVERING" not in plan and "idx_usage_user_date" in plan, plan


def test_the_index_holds_every_column_the_statement_touches(client, tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    cols = [r[2] for r in conn.execute(f"PRAGMA index_info({INDEX})")]
    conn.close()
    assert cols == ["user_id", "app_id", "request_timestamp", "estimated_cost_usd"]
    for col in cols:
        assert col in app_budget.MONTH_SPEND_SQL


def test_a_streamed_interviewer_turn_logs_its_preflight(client, free_user):
    """The line used to sit AFTER the n400 stream branch, which returned
    first, so the one lane where the wait mattered most logged nothing."""
    from unittest.mock import patch
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import ENVELOPE, _metadata, _stream_of

    seen: list[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record):
            if record.getMessage() == "chat_preflight":
                seen.append(record)

    # app.main calls logging.basicConfig(force=True) at import, which drops
    # pytest's caplog handler; attach to the emitting logger directly.
    log = logging.getLogger("app.routers.chat")
    grab = _Grab(); log.addHandler(grab)
    try:
        client.app.state.rate_limiter._buckets.clear()
        with patch("app.services.anthropic_or_fallback.route_stream_with_fallback",
                   _stream_of(json.dumps(ENVELOPE, ensure_ascii=False))):
            r = client.post("/v1/chat", headers={**free_user["headers"], "X-App-ID": "n400"},
                            json=chat_request(system_prompt="", user_content="on my own, about six years",
                                              stream=True, metadata=_metadata()))
        assert "event: envelope" in r.text
    finally:
        log.removeHandler(grab)
    assert len(seen) == 1, "a streamed interviewer turn must log chat_preflight exactly once"
    rec = seen[0]
    assert rec.call_type == "n400_interviewer_turn" and rec.streaming is True
    assert "after_budget_gates" in rec.marks_ms and "preflight_end" in rec.marks_ms
    assert rec.marks_ms["after_budget_gates"] <= rec.marks_ms["preflight_end"]
