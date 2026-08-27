"""The recall budget: a served dial, threaded to every leg, and every
attempt recorded durably.

Scott, 2026-08-27. The 500ms budget was the binding constraint, not
headroom: every degrade in the 3 days since #790 was a TIMEOUT on a
FULL-scope recall, and on 08-27 full scope ran 5 ok / 4 degraded (44%)
against 16 ok / 0 degraded for the People-scoped lane. He raised it to
1500, ruled it should be a dashboard dial rather than an env var, and
asked for a durable per-scope count so "did it help" is measurable —
the 44% could only be computed for ONE day because the denominator
lived in a container log that resets on every deploy.

These pin the three halves: the dial resolves and is bounded, the budget
reaches all three recall legs, and one row lands per attempt.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from app.services import context_quilt as cq
from app.services.recall_tuning import MAX_MS, MIN_MS, recall_timeout_ms

SEND = {
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "system_prompt": "BASE\n\n{{context_quilt}}\n\nNOTES",
    "user_content": "what did we decide about the routing item?",
    "context_quilt": True,
    "metadata": {"prompt_mode": "ProjectChat", "project_id": "p-1"},
}


class _S:
    cq_recall_timeout_ms = 500


@pytest.fixture
def cq_wire():
    """Patch the client recall() uses; capture the call kwargs."""
    post = AsyncMock()
    post.return_value = type("R", (), {
        "status_code": 200,
        "json": lambda self: {"context": "ctx", "matched_entities": ["A"], "patch_count": 3},
        "raise_for_status": lambda self: None})()
    http_client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        yield post


def _recall_call(post):
    calls = [c for c in post.call_args_list if c.args and c.args[0] == "/v1/recall"]
    assert calls, "no POST /v1/recall reached CQ"
    return calls[-1]


# --- the dial ------------------------------------------------------------------

def test_the_dial_wins_over_settings_and_absence_falls_back():
    assert recall_timeout_ms({"cq-recall": {"timeout_ms": 1500}}, _S) == 1500
    assert recall_timeout_ms({}, _S) == 500
    assert recall_timeout_ms({"cq-recall": {}}, _S) == 500
    assert recall_timeout_ms(None, _S) == 500


@pytest.mark.parametrize("bad", [0, -1, MIN_MS - 1, MAX_MS + 1, 150000, "1500", 1500.0, True, None])
def test_a_bad_dial_is_refused_and_the_settings_value_is_used(bad):
    """This number holds every chat turn. A 0 degrades every recall
    silently and a 150000 hangs the user for two and a half minutes, so
    an out-of-range dial must never reach the wire."""
    assert recall_timeout_ms({"cq-recall": {"timeout_ms": bad}}, _S) == 500


def test_the_bounds_are_inclusive_at_both_ends():
    assert recall_timeout_ms({"cq-recall": {"timeout_ms": MIN_MS}}, _S) == MIN_MS
    assert recall_timeout_ms({"cq-recall": {"timeout_ms": MAX_MS}}, _S) == MAX_MS


def test_the_shipped_bundle_carries_1500_and_is_server_only():
    d = json.loads(open("config/remote/cq-recall.json").read())
    assert d["timeout_ms"] == 1500 and d["server_only"] is True


def test_the_code_default_agrees_with_the_shipped_dial():
    """The code comment's standing rule: prod overrides via
    CZ_CQ_RECALL_TIMEOUT_MS, keep the two in agreement. A default that
    disagrees with the bundle is how a fresh environment silently runs a
    different budget than prod."""
    from app.config import Settings
    assert Settings.model_fields["cq_recall_timeout_ms"].default == 1500


def test_the_dial_is_not_fetchable_by_a_client(client):
    assert client.get("/v1/config/cq-recall").status_code == 404


# --- it reaches the wire -------------------------------------------------------

@pytest.mark.asyncio
async def test_the_budget_passed_in_is_the_budget_spent(cq_wire):
    # the fallback is READ, not assumed: this env sets its own value via
    # CZ_CQ_RECALL_TIMEOUT_MS, which is the very shadowing that makes the
    # code default and prod disagree if nobody keeps them in step
    from app.config import get_settings
    fallback = get_settings().cq_recall_timeout_ms / 1000.0
    await cq.recall(user_id="u", text="t", timeout_ms=1500, app_id="shouldersurf")
    assert _recall_call(cq_wire).kwargs["timeout"].read == 1.5
    await cq.recall(user_id="u", text="t", app_id="shouldersurf")
    assert _recall_call(cq_wire).kwargs["timeout"].read == fallback
    for bad in (0, -5, True, "1500"):
        await cq.recall(user_id="u", text="t", timeout_ms=bad, app_id="shouldersurf")
        assert _recall_call(cq_wire).kwargs["timeout"].read == fallback, bad


@pytest.mark.asyncio
async def test_a_successful_recall_reports_how_long_it_took_and_its_budget(cq_wire):
    r = await cq.recall(user_id="u", text="t", timeout_ms=1500, app_id="shouldersurf")
    assert r["timeout_ms"] == 1500 and isinstance(r["duration_ms"], int) and r["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_a_timeout_reports_the_budget_it_blew_and_never_raises():
    import httpx
    post = AsyncMock(side_effect=httpx.TimeoutException("too slow"))
    http_client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        r = await cq.recall(user_id="u", text="t", timeout_ms=1500, app_id="shouldersurf")
    assert r["degraded"] == "timeout" and r["timeout_ms"] == 1500
    assert r["context"] == "" and isinstance(r["duration_ms"], int)


def test_the_route_resolves_the_dial_and_every_leg_carries_it(client, plus_user, cq_wire):
    """Request-side: the value the operator set is the value on the wire,
    through the real chat route rather than a direct call."""
    from app.main import app as _app
    _app.state.remote_configs["cq-recall"] = {"version": 1, "server_only": True, "timeout_ms": 2500}
    try:
        assert client.post("/v1/chat", json=SEND, headers=plus_user["headers"]).status_code == 200
        assert _recall_call(cq_wire).kwargs["timeout"].read == 2.5
    finally:
        _app.state.remote_configs["cq-recall"] = {"version": 1, "server_only": True, "timeout_ms": 1500}


def test_the_people_scoped_leg_carries_it_too(client, free_user, cq_wire):
    """Free runs the People-scoped lane; it must spend the same budget,
    since Scott ruled uniform now and a split only if the data asks."""
    from app.main import app as _app
    _app.state.remote_configs["cq-recall"] = {"version": 1, "server_only": True, "timeout_ms": 2500}
    try:
        assert client.post("/v1/chat", json=SEND, headers=free_user["headers"]).status_code == 200
        call = _recall_call(cq_wire)
        assert call.kwargs["timeout"].read == 2.5
        assert (call.kwargs["json"].get("metadata") or {}).get("recall_scope") == "people"
    finally:
        _app.state.remote_configs["cq-recall"] = {"version": 1, "server_only": True, "timeout_ms": 1500}


# --- it is recorded ------------------------------------------------------------

def _obs(db_path):
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM recall_observations ORDER BY created_at")]
    conn.close()
    return rows


def test_every_attempt_lands_one_durable_row_with_its_scope_and_budget(client, plus_user, cq_wire, tmp_db_path):
    assert client.post("/v1/chat", json=SEND, headers=plus_user["headers"]).status_code == 200
    rows = _obs(tmp_db_path)
    assert len(rows) == 1, "a successful recall must be counted too, or there is no denominator"
    r = rows[0]
    assert r["scope"] == "full" and r["outcome"] == "ok" and r["timeout_ms"] == 1500
    assert r["tier"] == "plus" and r["matched"] == 1 and r["patch_count"] == 3
    assert isinstance(r["duration_ms"], int)


def test_a_degraded_attempt_is_recorded_as_its_reason(client, plus_user, tmp_db_path):
    import httpx
    post = AsyncMock(side_effect=httpx.TimeoutException("too slow"))
    http_client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        assert client.post("/v1/chat", json=SEND, headers=plus_user["headers"]).status_code == 200
    rows = _obs(tmp_db_path)
    assert len(rows) == 1 and rows[0]["outcome"] == "timeout" and rows[0]["scope"] == "full"


def test_the_rate_that_decides_the_budget_is_one_query(client, plus_user, free_user, cq_wire, tmp_db_path):
    """The whole point: degraded-over-attempts per scope, from stored rows."""
    for _ in range(2):
        client.post("/v1/chat", json=SEND, headers=plus_user["headers"])
    client.post("/v1/chat", json=SEND, headers=free_user["headers"])
    conn = sqlite3.connect(tmp_db_path)
    got = dict(conn.execute(
        "SELECT scope, COUNT(*) FROM recall_observations GROUP BY scope").fetchall())
    conn.close()
    assert got == {"full": 2, "people": 1}


@pytest.mark.asyncio
async def test_observations_purge_at_the_same_thirty_days_as_everything_else(client, tmp_db_path):
    import uuid
    from datetime import datetime, timedelta, timezone
    import aiosqlite
    from app.services.recall_tuning import purge_observations
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(tmp_db_path) as db:
        for ts in (old, new):
            await db.execute(
                "INSERT INTO recall_observations (id, created_at, scope, outcome) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), ts, "full", "ok"))
        await db.commit()
        assert await purge_observations(db) == 1
    assert len(_obs(tmp_db_path)) == 1


def test_a_telemetry_failure_never_breaks_the_turn(client, plus_user, cq_wire):
    from app.services import recall_tuning
    with patch.object(recall_tuning, "record_observation",
                      AsyncMock(side_effect=RuntimeError("disk full"))):
        assert client.post("/v1/chat", json=SEND, headers=plus_user["headers"]).status_code == 200
