"""Time to first token, recorded where it can be measured and NULL where
it cannot.

Before this, TTFT existed nowhere: usage_log carried only the whole wall
clock in response_time_ms, so "how long does a person wait before the
first word appears" could not be answered from the record. The only place
a first token is observable is the Anthropic stream adapter, so that is
where it is stamped, and the value rides the stream's done event to the
chat router, which is the one caller that passes it to the usage row.

Three properties are pinned here, each with the mistake it guards:

  1. The stamp lands on the FIRST CONTENT DELTA, not on message_start.
     message_start arrives before any generation and would make every
     TTFT look better than what the user saw.
  2. A stream with no content records None, never 0 and never the total.
     0 reads as "instant"; the total reads as "all wait"; both are made up.
  3. The dashboard's coverage counts NULL rows in the denominator and the
     percentiles exclude them. Coverage over non-null rows only is always
     1.0, which is a number that cannot be false and therefore says nothing.
"""

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import aiosqlite
import pytest
from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse
from app.services.providers.anthropic import AnthropicAdapter

ADMIN = {"X-Admin-Key": "test-admin-key"}


# ---------------------------------------------------------------------------
# Adapter: where the stamp lands
# ---------------------------------------------------------------------------

def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(
        api_key="test",
        base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key",
        auth_prefix="",
    )


class _Clock:
    """A monotonic clock the fake stream advances by hand. Patched in as the
    adapter module's `time` so asyncio's own clock is untouched."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def _drain(timed_lines, *, raise_after=None):
    """Feed (t_seconds, sse_line) pairs through send_request_stream with the
    clock set to t before each line is yielded. Returns the done event."""
    clock = _Clock()

    async def fake_post_stream(_self, _url, _body, _headers):
        for t, line in timed_lines:
            clock.now = t
            yield line
        if raise_after is not None:
            raise raise_after

    req = ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="sys", user_content="hi", stream=True,
    )

    async def run():
        events = []
        with patch.object(AnthropicAdapter, "_post_stream", fake_post_stream), \
                patch("app.services.providers.anthropic.time", clock):
            async for ev in _adapter().send_request_stream(req):
                events.append(ev)
        return events

    events = asyncio.run(run())
    done = [e for e in events if e.get("done")]
    assert done, f"stream produced no done event: {events!r}"
    return done[-1]


_MESSAGE_START = (
    'data: {"type":"message_start","message":{"id":"msg_1",'
    '"model":"claude-sonnet-4-6","usage":{"input_tokens":11}}}'
)
_PING = 'data: {"type":"ping"}'
_TEXT_BLOCK_START = (
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}'
)


def _text_delta(text: str) -> str:
    return ('data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":%s}}' % json.dumps(text))


_MESSAGE_DELTA = (
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":42}}'
)
_MESSAGE_STOP = 'data: {"type":"message_stop"}'


def test_ttft_is_measured_to_the_first_text_delta_not_message_start():
    """message_start at 100ms, first text at 750ms. The answer is 750."""
    done = _drain([
        (0.10, _MESSAGE_START),
        (0.20, _PING),
        (0.30, _TEXT_BLOCK_START),
        (0.75, _text_delta("Hel")),
        (0.90, _text_delta("lo")),
        (1.00, _MESSAGE_DELTA),
        (1.10, _MESSAGE_STOP),
    ])
    assert done["ttft_ms"] == 750, (
        "TTFT must be stamped on the first content delta; 100 would mean "
        "message_start was stamped and the user's wait was under-reported")
    assert isinstance(done["ttft_ms"], int)
    # output_tokens on the streamed response is untouched by the stamp, so
    # (response_time_ms - ttft_ms) and output_tokens can give throughput.
    assert done["response"].output_tokens == 42
    assert done["response"].text == "Hello"


def test_a_stream_with_no_content_records_none_not_zero_and_not_the_total():
    done = _drain([
        (0.10, _MESSAGE_START),
        (0.20, _PING),
        (1.10, _MESSAGE_STOP),
    ])
    assert done["ttft_ms"] is None, (
        f"no token ever arrived, so ttft_ms must be None, got {done['ttft_ms']!r}")


def test_a_stream_that_errors_before_any_delta_never_yields_a_ttft():
    """The adapter raises before a done event exists, so the router logs an
    error row with response=None and no ttft, which lands as NULL (proved
    on the tracker below). What is pinned here: no done event carrying a
    number escapes from a stream that produced no content."""
    with pytest.raises(HTTPException) as exc:
        _drain([
            (0.10, _MESSAGE_START),
            (0.20, _PING),
        ], raise_after=RuntimeError("connection reset"))
    assert exc.value.status_code == 502


def test_a_tool_use_stream_counts_the_first_input_json_delta():
    """A tool call answers with input_json_delta, never text_delta. The
    first of those is the first token the caller can act on."""
    done = _drain([
        (0.10, _MESSAGE_START),
        (0.30, 'data: {"type":"content_block_start","index":0,'
               '"content_block":{"type":"tool_use","id":"tu_1","name":"f","input":{}}}'),
        (0.50, 'data: {"type":"content_block_delta","index":0,'
               '"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":"}}'),
        (0.60, 'data: {"type":"content_block_delta","index":0,'
               '"delta":{"type":"input_json_delta","partial_json":"1}"}}'),
        (1.00, 'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
               '"usage":{"output_tokens":7}}'),
        (1.10, _MESSAGE_STOP),
    ])
    assert done["ttft_ms"] == 500


def test_thinking_deltas_do_not_count_as_the_first_token():
    """Thinking is generation the user never sees. The wait ends at the
    first visible byte."""
    done = _drain([
        (0.10, _MESSAGE_START),
        (0.20, 'data: {"type":"content_block_delta","index":0,'
               '"delta":{"type":"thinking_delta","thinking":"hmm"}}'),
        (0.80, _text_delta("Hi")),
        (1.00, _MESSAGE_DELTA),
        (1.10, _MESSAGE_STOP),
    ])
    assert done["ttft_ms"] == 800


def test_the_base_adapter_fallback_stream_reports_none():
    """Every non-Anthropic provider streams through the base fallback,
    which is one non-streaming call. It must say None, not omit the key."""
    from app.services.providers.base import ProviderAdapter

    class _Fake(ProviderAdapter):
        async def send_request(self, request):
            return ChatResponse(text="whole", model="m", provider="p",
                                input_tokens=1, output_tokens=2)

    async def run():
        out = []
        async for ev in _Fake("k", "u", "h", "").send_request_stream(
                ChatRequest(provider="p", model="m", system_prompt="s", user_content="u")):
            out.append(ev)
        return out

    events = asyncio.run(run())
    assert events[-1]["done"] is True
    assert "ttft_ms" in events[-1] and events[-1]["ttft_ms"] is None


# ---------------------------------------------------------------------------
# Migration + tracker: the column, and who writes NULL
# ---------------------------------------------------------------------------

async def _old_schema_db(path: str) -> aiosqlite.Connection:
    """A database built from SCHEMA_SQL alone, which predates ttft_ms."""
    from app.database import SCHEMA_SQL
    db = await aiosqlite.connect(path)
    await db.executescript(SCHEMA_SQL)
    return db


async def _columns(db, table="usage_log") -> set[str]:
    return {r[1] for r in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}


def _insert_user_sync(path: str, user_id: str) -> None:
    from tests.conftest import _insert_user
    _insert_user(path, user_id=user_id, tier="pro", monthly_limit=5.0)


@pytest.mark.asyncio
async def test_migration_adds_ttft_ms_and_keeps_existing_rows(tmp_path):
    from app.database import apply_migrations

    path = str(tmp_path / "t.db")
    db = await _old_schema_db(path)
    assert "ttft_ms" not in await _columns(db), "the fresh schema must predate the column"

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO users (id, apple_sub, email, tier, created_at, updated_at, is_active) "
        "VALUES ('u', 'a', 'e', 'pro', ?, ?, 1)", (now, now))
    await db.execute(
        "INSERT INTO usage_log (id, user_id, provider, model, request_timestamp, "
        "response_time_ms, status, output_tokens) VALUES ('r1', 'u', 'p', 'm', ?, 1500, 'success', 9)",
        (now,))
    await db.commit()

    first = await apply_migrations(db)
    assert first["failed"] == 0
    assert "ttft_ms" in await _columns(db)
    row = await (await db.execute(
        "SELECT response_time_ms, output_tokens, ttft_ms FROM usage_log WHERE id = 'r1'")).fetchone()
    assert tuple(row) == (1500, 9, None), "an existing row keeps its values and reads NULL"
    await db.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent_on_both_paths(tmp_path):
    """Fast path (fingerprint match) and full sweep (record removed) must
    both leave one ttft_ms column and report no failure."""
    from app.database import apply_migrations

    db = await _old_schema_db(str(tmp_path / "t.db"))
    await apply_migrations(db)
    assert (await apply_migrations(db))["path"] == "skipped"
    await db.execute("DELETE FROM schema_state")
    swept = await apply_migrations(db)
    assert swept["path"] == "swept" and swept["failed"] == 0
    cols = [r[1] for r in await (await db.execute("PRAGMA table_info(usage_log)")).fetchall()]
    assert cols.count("ttft_ms") == 1
    await db.close()


def _req(**meta) -> ChatRequest:
    return ChatRequest(provider="anthropic", model="claude-sonnet-4-6",
                       system_prompt="s", user_content="u", metadata=meta or None)


def _resp(output_tokens=42) -> ChatResponse:
    return ChatResponse(text="t", model="claude-sonnet-4-6", provider="anthropic",
                        input_tokens=10, output_tokens=output_tokens,
                        usage={"input_tokens": 10, "output_tokens": output_tokens})


async def _migrated_db_with_user(path: str) -> aiosqlite.Connection:
    from app.database import apply_migrations
    db = await _old_schema_db(path)
    await apply_migrations(db)
    await db.commit()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO users (id, apple_sub, email, tier, created_at, updated_at, is_active) "
        "VALUES ('u', 'a', 'e', 'pro', ?, ?, 1)", (now, now))
    await db.commit()
    return db


async def _only_row(db):
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute("SELECT ttft_ms, output_tokens, status FROM usage_log")).fetchall()
    assert len(rows) == 1
    return rows[0]


@pytest.mark.asyncio
async def test_a_non_streaming_record_writes_null(tmp_path):
    from app.services.usage_tracker import UsageTracker

    db = await _migrated_db_with_user(str(tmp_path / "t.db"))
    await UsageTracker().log_usage(db, "u", _req(call_type="report"), _resp(), 2200)
    row = await _only_row(db)
    assert row["ttft_ms"] is None, "a call that never passed a ttft must store NULL"
    assert row["output_tokens"] == 42
    await db.close()


@pytest.mark.asyncio
async def test_a_streaming_record_writes_the_integer_and_keeps_output_tokens(tmp_path):
    from app.services.usage_tracker import UsageTracker

    db = await _migrated_db_with_user(str(tmp_path / "t.db"))
    await UsageTracker().log_usage(
        db, "u", _req(call_type="query"), _resp(output_tokens=42), 2200, ttft_ms=321)
    row = await _only_row(db)
    assert row["ttft_ms"] == 321
    assert row["output_tokens"] == 42, (
        "throughput later needs output_tokens next to ttft_ms on the same row")
    await db.close()


@pytest.mark.asyncio
async def test_an_error_row_with_no_response_writes_null(tmp_path):
    """The shape the router logs when the stream raised before its first
    token: response=None, status=error, nothing passed for ttft."""
    from app.services.usage_tracker import UsageTracker

    db = await _migrated_db_with_user(str(tmp_path / "t.db"))
    await UsageTracker().log_usage(db, "u", _req(call_type="query"), None, 900, status="error")
    row = await _only_row(db)
    assert row["ttft_ms"] is None and row["status"] == "error"
    await db.close()


# ---------------------------------------------------------------------------
# Router: the value actually travels from the done event to the row
# ---------------------------------------------------------------------------

def _latest_row(db_path: str, user_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT ttft_ms, output_tokens, status, call_type FROM usage_log "
        "WHERE user_id = ? ORDER BY request_timestamp DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()
    assert row is not None, "no usage_log row was written"
    return row


def test_the_streamed_chat_path_carries_ttft_from_the_done_event_to_the_row(
        client, pro_user, tmp_db_path):
    """A response-side check cannot see a request-side hole: the adapter
    can stamp perfectly and the router can still drop the key on the way
    to log_usage. This walks the whole hop."""
    async def fake_stream(_body):
        yield {"type": "text", "text": "streamed", "done": False}
        yield {
            "type": "text", "text": "", "done": True,
            "response": ChatResponse(
                text="streamed", input_tokens=10, output_tokens=5,
                model="claude-haiku-4-5-20251001", provider="anthropic",
                usage={"input_tokens": 10, "output_tokens": 5}),
            "ttft_ms": 321,
        }

    with patch("app.services.provider_router.ProviderRouter.route_stream",
               side_effect=lambda body: fake_stream(body)):
        resp = client.post("/v1/chat", headers=pro_user["headers"], json={
            "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
            "system_prompt": "you help", "user_content": "hi", "stream": True,
            "metadata": {"prompt_mode": "PostMeetingChat", "call_type": "query"},
        })
    assert resp.status_code == 200
    assert '"done"' in resp.text
    row = _latest_row(tmp_db_path, pro_user["user_id"])
    assert row["status"] == "success"
    assert row["ttft_ms"] == 321
    assert row["output_tokens"] == 5


def test_the_non_streaming_chat_path_writes_null(client, pro_user, mock_provider, tmp_db_path):
    resp = client.post("/v1/chat", headers=pro_user["headers"], json={
        "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
        "system_prompt": "you help", "user_content": "hi",
        "metadata": {"call_type": "query"},
    })
    assert resp.status_code == 200
    assert "ttft_ms" not in resp.json(), "instrumentation must not change the wire body"
    row = _latest_row(tmp_db_path, pro_user["user_id"])
    assert row["status"] == "success"
    assert row["ttft_ms"] is None


# ---------------------------------------------------------------------------
# Dashboard: percentiles over measured rows, coverage over all of them
# ---------------------------------------------------------------------------

def _insert_usage(db_path, user_id, *, model, call_type, ttft, status="success",
                  latency=1000, app_id="shouldersurf"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, call_type, app_id, ttft_ms)
           VALUES (?, ?, 'anthropic', ?, 100, 50, 0.01, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, model, datetime.now(timezone.utc).isoformat(),
         latency, status, call_type, app_id, ttft),
    )
    conn.commit()
    conn.close()


def _seed_mixed(db_path, user_id):
    # Group "streamed": five measured (100..1000) and five NULL success
    # rows, plus one ERROR row carrying a ttft that must count nowhere.
    for t in (100, 200, 300, 400, 1000):
        _insert_usage(db_path, user_id, model="m-streamed", call_type="query", ttft=t)
    for _ in range(5):
        _insert_usage(db_path, user_id, model="m-streamed", call_type="query", ttft=None)
    _insert_usage(db_path, user_id, model="m-streamed", call_type="query", ttft=5, status="error")
    # Group "batch": three rows, none measured.
    for _ in range(3):
        _insert_usage(db_path, user_id, model="m-batch", call_type="report", ttft=None)


def test_dashboard_by_model_percentiles_exclude_nulls_and_coverage_counts_them(
        client, tmp_db_path):
    _insert_user_sync(tmp_db_path, "ttft-user")
    _seed_mixed(tmp_db_path, "ttft-user")

    payload = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()
    by_model = {r["model"]: r for r in payload["by_model"]}

    streamed = by_model["m-streamed"]
    assert streamed["requests"] == 10, "success rows only; the error row is not counted"
    assert streamed["ttft_coverage"] == 0.5, (
        "five of ten success rows carry a ttft; a coverage of 1.0 would mean "
        "the denominator was the measured rows, which cannot be false")
    # nearest rank over [100, 200, 300, 400, 1000]
    assert streamed["ttft_p50_ms"] == 300
    assert streamed["ttft_p95_ms"] == 1000
    # the wall-clock average is untouched
    assert streamed["avg_latency_ms"] == 1000

    batch = by_model["m-batch"]
    assert batch["ttft_coverage"] == 0.0
    assert batch["ttft_p50_ms"] is None and batch["ttft_p95_ms"] is None, (
        "an unmeasured group must read None, never 0")


def test_dashboard_by_call_type_carries_the_same_three_keys(client, tmp_db_path):
    _insert_user_sync(tmp_db_path, "ttft-user2")
    _seed_mixed(tmp_db_path, "ttft-user2")

    payload = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()
    by_ct = {r["call_type"]: r for r in payload["by_call_type"]}
    assert by_ct["query"]["ttft_coverage"] == 0.5
    assert by_ct["query"]["ttft_p50_ms"] == 300
    assert by_ct["query"]["ttft_p95_ms"] == 1000
    assert by_ct["report"]["ttft_coverage"] == 0.0
    assert by_ct["report"]["ttft_p50_ms"] is None


def test_dashboard_percentiles_honor_the_app_filter(client, tmp_db_path):
    _insert_user_sync(tmp_db_path, "ttft-user3")
    _insert_usage(tmp_db_path, "ttft-user3", model="m", call_type="query", ttft=100,
                  app_id="shouldersurf")
    _insert_usage(tmp_db_path, "ttft-user3", model="m", call_type="query", ttft=9000,
                  app_id="techrehearsal")

    ss = client.get("/webhooks/admin/dashboard?days=7&app=shouldersurf", headers=ADMIN).json()
    assert {r["model"]: r for r in ss["by_model"]}["m"]["ttft_p95_ms"] == 100
    tr = client.get("/webhooks/admin/dashboard?days=7&app=techrehearsal", headers=ADMIN).json()
    assert {r["model"]: r for r in tr["by_model"]}["m"]["ttft_p95_ms"] == 9000


def test_percentile_helper_nearest_rank():
    from app.routers.webhooks import _percentile
    assert _percentile([], 0.5) is None
    assert _percentile([7], 0.95) == 7
    assert _percentile([100, 200, 300, 400, 1000], 0.5) == 300
    assert _percentile([100, 200, 300, 400, 1000], 0.95) == 1000
    assert _percentile(list(range(1, 101)), 0.95) == 95
