"""A stream_timeout must free its turn id at once (Fable auditor, 2026-11-16).

The sentence stream's wall clock cancels the turn task, and CancelledError is
not an Exception, so the tracked tail's `except Exception` missed it: no
finish, no abandon, and the id read in_progress until the 240s valve."""
import asyncio
from unittest.mock import patch

from app.services import chat_turns
from tests.test_turn_id_released_after_refusal import HDR, STREAM_ROUTE, _body


def _hanging(provider_router, request, db, settings):
    async def gen():
        yield {"type": "text", "text": "Thanks. "}
        await asyncio.sleep(30)
        yield {"type": "text", "text": "", "done": True}
    return gen()


def test_a_stream_timeout_frees_its_turn_id(client, free_user):
    client.app.state.rate_limiter._buckets.clear()
    hdr = {**free_user["headers"], **HDR}
    with patch(STREAM_ROUTE, _hanging), \
         patch("app.routers.chat._CHAT_STREAM_WALL_CLOCK_SECONDS", 0.3), \
         patch("app.routers.chat._PROGRESS_TICK_SECONDS", 0.1):
        r = client.post("/v1/chat", json=_body("t-timeout"), headers=hdr)
    assert "stream_timeout" in r.text, r.text[:300]
    assert chat_turns.running_info(free_user["user_id"], "t-timeout") is None
