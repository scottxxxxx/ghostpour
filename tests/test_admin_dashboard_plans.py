"""The admin dashboard must never scan usage_log, and the index that stops it
must not change a single number.

Measured on the live dashboard 2026-11-01: GET /webhooks/admin/dashboard?days=7
took 0.4 to 1.5 s warm and 24 to 29 s on the first call after a container
restart, which is every deploy. The handler runs about a dozen windowed
aggregates over usage_log filtered on request_timestamp alone, and no index
led with that column (every composite index led with user_id or app_id), so
each one was a full scan of a table whose rows carry the raw request and
response text. Cold, that is most of a 636 MB file read a dozen times.

Two halves here. The ORACLE pins every aggregate the endpoint returns on a
fixed seed, written against the code BEFORE the index existed, so the index
is proved to change the plan and nothing else. The PLAN tests capture the
statements the handler really issues (a tracing hook on aiosqlite's execute,
so the SQL cannot drift from a copy in this file), run EXPLAIN QUERY PLAN on
each against the migrated test database, and fail on any line that scans
usage_log. The last test drops the index and shows the scan come back, so
this file fails for the right reason if the index is ever removed.
"""

import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/dashboard"
INDEX = "idx_usage_date_dash"

# A plan line that reads the whole table, under its own name or the `l`
# alias the top-users join gives it. "SCAN users" (the exclusion subquery)
# is a different table and must not match.
SCANS_USAGE_LOG = re.compile(r"\bSCAN (usage_log|l)\b")


# ---------------------------------------------------------------------------
# Seed: 300 rows over 30 days, two apps, five users. Every value is a
# function of the row index so the oracle below is reproducible by hand.
# Rows sit at 12:00 UTC plus a few minutes on their day, so the 7-day window
# (date('now', '-7 days') is midnight UTC seven days ago) always holds days 0
# to 7 inclusive and never day 8, whatever time of day the test runs.
# ---------------------------------------------------------------------------

APPS = ("shouldersurf", "n400")
USERS = ("dash-u0", "dash-u1", "dash-u2", "dash-u3", "dash-u4")


def _row(i: int, midnight: datetime) -> tuple:
    day = i % 30
    ts = midnight - timedelta(days=day) + timedelta(hours=12, minutes=i % 60)
    if i % 11 == 0:
        status = "error"
    elif i % 23 == 0:
        status = "rate_limited"
    else:
        status = "success"
    provider, model = (
        ("openai", "gpt-5-mini") if i % 3 == 0 else ("anthropic", "claude-haiku-4-5"))
    call_type = None if i % 13 == 0 else ("chat", "summary", "report")[i % 3]
    scenario = None if i % 4 == 0 else ("interview", "pitch")[i % 2]
    ttft = 50 + i % 40 if i % 4 == 1 else None
    return (
        str(uuid.uuid4()), USERS[i % 5], provider, model,
        100 + i, 50 + (i % 17), round(0.001 * (1 + i % 7), 6),
        ts.isoformat(), 100 + (i * 37) % 900, status,
        "boom" if status != "success" else None, call_type,
        (i % 5) * 10, APPS[i % 2], scenario, ttft,
    )


def _seed(db_path: str) -> None:
    from tests.conftest import _insert_user
    for uid in USERS:
        _insert_user(db_path, user_id=uid, tier="pro", monthly_limit=5.10)
    conn = sqlite3.connect(db_path)
    # One automation account (out of by_call_type and by_scenario, in the
    # totals) and one excluded account (out of everything), so both
    # exclusion subqueries are exercised by the oracle.
    conn.execute("UPDATE users SET tier = 'automation' WHERE id = 'dash-u3'")
    conn.execute("UPDATE users SET exclude_from_stats = 1 WHERE id = 'dash-u4'")
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    conn.executemany(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, error_message, call_type, cached_tokens, app_id, scenario, ttft_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [_row(i, midnight) for i in range(300)],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def seeded(client, tmp_db_path):
    _seed(tmp_db_path)
    return client


# ---------------------------------------------------------------------------
# The tracing hook: every statement the request runs that names usage_log,
# with the parameters it was bound with, so the plan tests explain exactly
# what the handler executed and nothing this file retyped.
# ---------------------------------------------------------------------------

@pytest.fixture
def traced(monkeypatch):
    seen: list[tuple[str, tuple]] = []
    original = aiosqlite.Connection.execute

    async def execute(self, sql, parameters=None):
        if "usage_log" in sql:
            seen.append((sql, tuple(parameters or ())))
        return await original(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
    return seen


def _plan(db_path: str, sql: str, params: tuple) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    finally:
        conn.close()
    return " | ".join(r[-1] for r in rows)


def _windowed(statements: list[tuple[str, tuple]]) -> list[tuple[str, tuple]]:
    """The statements the index exists for: a request_timestamp range and
    no user equality. The per-user allocation alert reads that run inside
    the same request (`WHERE user_id = ? AND request_timestamp >= ?`) are
    user-scoped and have had their own covering index since #1018."""
    return [(s, p) for s, p in statements
            if "request_timestamp >=" in s and "user_id = ?" not in s]


def _scanning(db_path: str, statements: list[tuple[str, tuple]]) -> list[str]:
    """One line per statement whose plan reads all of usage_log."""
    bad = []
    for sql, params in statements:
        plan = _plan(db_path, sql, params)
        if SCANS_USAGE_LOG.search(plan):
            bad.append(f"{' '.join(sql.split())[:110]}\n    plan: {plan}")
    return bad


# Distinctive fragments of the handler's windowed statements, so a capture
# that saw nothing (a renamed method, a hook that never attached) fails here
# instead of passing an empty list through the scan check.
HANDLER_FRAGMENTS = (
    "GROUP BY provider, model",
    "GROUP BY call_type",
    "GROUP BY scenario",
    "GROUP BY u.id",
    "GROUP BY date(request_timestamp)",
    "ORDER BY response_time_ms",
    "SUM(cached_tokens)",
    "ttft_ms IS NOT NULL",
)


def test_the_hook_saw_the_handlers_windowed_statements(seeded, tmp_db_path, traced):
    assert seeded.get(URL, params={"days": 7}, headers=ADMIN).status_code == 200
    joined = "\n".join(" ".join(sql.split()) for sql, _ in traced)
    for frag in HANDLER_FRAGMENTS:
        assert frag in joined, f"the hook never saw {frag!r}"
    assert len(traced) >= 11, len(traced)


def test_no_dashboard_statement_scans_usage_log(seeded, tmp_db_path, traced):
    assert seeded.get(URL, params={"days": 7}, headers=ADMIN).status_code == 200
    bad = _scanning(tmp_db_path, traced)
    assert not bad, "statements that scan usage_log:\n" + "\n".join(bad)


def test_the_windowed_aggregates_are_covered_by_the_index(seeded, tmp_db_path, traced):
    """Covered, not just searched: a SEARCH that fetched the table row for
    every hit would still read the wide rows of the whole window. The
    exclusion subquery's SCAN of users is a different, small table."""
    assert seeded.get(URL, params={"days": 7}, headers=ADMIN).status_code == 200
    windowed = _windowed(traced)
    assert len(windowed) >= 11, len(windowed)
    for sql, params in windowed:
        plan = _plan(tmp_db_path, sql, params)
        assert f"USING COVERING INDEX {INDEX}" in plan, (" ".join(sql.split())[:110], plan)


def test_the_app_filtered_dashboard_does_not_scan_either(seeded, tmp_db_path, traced):
    assert seeded.get(URL, params={"days": 7, "app": "n400"}, headers=ADMIN).status_code == 200
    bad = _scanning(tmp_db_path, traced)
    assert not bad, "statements that scan usage_log:\n" + "\n".join(bad)


def test_latency_trends_is_served_by_the_same_index(seeded, tmp_db_path, traced):
    r = seeded.get("/webhooks/admin/latency-trends", params={"days": 7}, headers=ADMIN)
    assert r.status_code == 200
    windowed = _windowed(traced)
    assert windowed
    for sql, params in windowed:
        plan = _plan(tmp_db_path, sql, params)
        assert f"USING COVERING INDEX {INDEX}" in plan, plan


def test_without_the_index_every_windowed_statement_scans(seeded, tmp_db_path, traced):
    """The shape of the defect, so this file fails for the right reason if
    the index is dropped: with it gone, every windowed statement is back to
    reading the whole table."""
    assert seeded.get(URL, params={"days": 7}, headers=ADMIN).status_code == 200
    windowed = _windowed(traced)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(f"DROP INDEX {INDEX}")
    conn.commit()
    conn.close()
    bad = _scanning(tmp_db_path, windowed)
    assert len(bad) == len(windowed), (len(bad), len(windowed), bad)


def test_the_index_holds_every_column_the_handler_reads(client, tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    cols = [r[2] for r in conn.execute(f"PRAGMA index_info({INDEX})")]
    conn.close()
    assert cols == [
        "request_timestamp", "status", "app_id", "user_id", "provider", "model",
        "call_type", "scenario", "input_tokens", "output_tokens", "cached_tokens",
        "estimated_cost_usd", "response_time_ms", "ttft_ms",
    ]


# ---------------------------------------------------------------------------
# The oracle. Captured from the endpoint on the code BEFORE the index was
# added (2026-11-01), then held fixed through the change. `generated_at` is
# the only key not compared; `daily_usage` days are relative to today.
# ---------------------------------------------------------------------------

def _expected(today: datetime) -> dict:
    def day(n: int) -> str:
        return (today - timedelta(days=n)).strftime("%Y-%m-%d")
    return {
        "users": {"total": 4, "active": 4, "by_tier": {"automation": 1, "pro": 3},
                  "people": 4, "anonymous": {"accounts": 0, "people": 0, "with_plan": 0}},
        "today": {"requests": 9, "tokens": 2775, "cost_usd": 0.036},
        "usage": {"period_days": 7, "total_requests": 70, "successful": 61, "errors": 7,
                  "rate_limited": 2, "total_input_tokens": 16690,
                  "total_output_tokens": 4044, "total_tokens": 20734,
                  "total_cost_usd": 0.275, "avg_latency_ms": 529,
                  "max_latency_ms": 991, "min_latency_ms": 100},
        "by_model": [
            {"provider": "anthropic", "model": "claude-haiku-4-5", "requests": 35,
             "input_tokens": 8133, "output_tokens": 2048, "cost_usd": 0.139,
             "avg_latency_ms": 498, "ttft_p50_ms": 67, "ttft_p95_ms": 87,
             "ttft_coverage": 0.4},
            {"provider": "openai", "model": "gpt-5-mini", "requests": 26,
             "input_tokens": 6365, "output_tokens": 1495, "cost_usd": 0.102,
             "avg_latency_ms": 543, "ttft_p50_ms": 63, "ttft_p95_ms": 83,
             "ttft_coverage": 0.1538},
        ],
        "by_call_type": [
            {"call_type": "summary", "requests": 16, "tokens": 4679, "cost_usd": 0.064,
             "avg_latency_ms": 535, "ttft_p50_ms": 71, "ttft_p95_ms": 87,
             "ttft_coverage": 0.5625},
            {"call_type": "chat", "requests": 16, "tokens": 4799, "cost_usd": 0.066,
             "avg_latency_ms": 550, "ttft_p50_ms": None, "ttft_p95_ms": None,
             "ttft_coverage": 0.0},
            {"call_type": "report", "requests": 15, "tokens": 4276, "cost_usd": 0.067,
             "avg_latency_ms": 441, "ttft_p50_ms": 55, "ttft_p95_ms": 75,
             "ttft_coverage": 0.2667},
            {"call_type": "(untyped)", "requests": 5, "tokens": 1535, "cost_usd": 0.011,
             "avg_latency_ms": 543, "ttft_p50_ms": 75, "ttft_p95_ms": 75,
             "ttft_coverage": 0.2},
        ],
        "by_scenario": [
            {"scenario": "pitch", "requests": 27, "tokens": 7878, "cost_usd": 0.103},
            {"scenario": "interview", "requests": 13, "tokens": 3750, "cost_usd": 0.05},
            {"scenario": "(untagged)", "requests": 12, "tokens": 3661, "cost_usd": 0.055},
        ],
        "top_users": [
            {"user_id": "dash-u0", "email": "dash-u0@test.com", "tier": "pro", "requests": 18,
             "total_tokens": 5321, "cost_usd": 0.071, "last_request": f"{day(0)}T12:30:00+00:00"},
            {"user_id": "dash-u1", "email": "dash-u1@test.com", "tier": "pro", "requests": 17,
             "total_tokens": 5005, "cost_usd": 0.07, "last_request": f"{day(1)}T12:31:00+00:00"},
            {"user_id": "dash-u2", "email": "dash-u2@test.com", "tier": "pro", "requests": 17,
             "total_tokens": 4963, "cost_usd": 0.067, "last_request": f"{day(2)}T12:32:00+00:00"},
            {"user_id": "dash-u3", "email": "dash-u3@test.com", "tier": "automation", "requests": 9,
             "total_tokens": 2752, "cost_usd": 0.033, "last_request": f"{day(3)}T12:33:00+00:00"},
        ],
        "latency_percentiles": {"p50": 497, "p75": 730, "p90": 915, "p95": 940, "p99": 991},
        "allocation_alerts": [],
        "trials": {"active_trials": 0, "converted_subscribers": 4, "trial_users": []},
        "cache_savings": {"cached_tokens": 780, "estimated_savings_usd": 0.0006},
        # Day 4 is absent on purpose: every row that day belongs to the
        # excluded account (30 days and 5 users share a factor), so the
        # exclusion subquery is what keeps it out.
        "daily_usage": [
            {"day": day(7), "requests": 10, "cost": 0.037, "errors": 1},
            {"day": day(6), "requests": 10, "cost": 0.041, "errors": 1},
            {"day": day(5), "requests": 10, "cost": 0.038, "errors": 1},
            {"day": day(3), "requests": 10, "cost": 0.039, "errors": 1},
            {"day": day(2), "requests": 10, "cost": 0.043, "errors": 1},
            {"day": day(1), "requests": 10, "cost": 0.04, "errors": 1},
            {"day": day(0), "requests": 10, "cost": 0.037, "errors": 1},
        ],
    }


def test_every_aggregate_is_unchanged_by_the_index(seeded):
    body = seeded.get(URL, params={"days": 7}, headers=ADMIN).json()
    body.pop("generated_at")
    expected = _expected(datetime.now(timezone.utc))
    assert set(body) == set(expected), set(body) ^ set(expected)
    for key in expected:
        assert body[key] == expected[key], key
