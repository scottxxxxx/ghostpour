"""A turn id must not read `turn_in_progress` when nothing is running.

Found 2026-09-20 building the N-400 stream client: a send the rate limiter
refused came back 429, and the SAME turn_id sent again answered
`turn_in_progress` for up to 240 seconds. The route wrapper now releases the
id on every exit that did not run the turn. The other half matters as much:
it must NOT release an id whose turn is still running, because a resend
would then start a second upstream call, which is the double bill turn ids
exist to stop.
"""
import json
from unittest.mock import patch

from app.services import chat_turns
from tests.test_n400_sentence_stream_route import ENVELOPE, _metadata, _stream_of

STREAM_ROUTE = "app.services.anthropic_or_fallback.route_stream_with_fallback"
HDR = {"X-App-ID": "n400"}


def _body(turn_id, **over):
    from tests.conftest import chat_request
    b = chat_request(system_prompt="", user_content="on my own, about six years",
                     stream=True, metadata=_metadata(), turn_id=turn_id)
    b.update(over)
    return b


def _exhaust_rate_limit(client, free_user):
    limiter = client.app.state.rate_limiter
    limiter._buckets.clear()
    for _ in range(50):
        if not limiter.check(free_user["user_id"], 5)[0]:
            return
    raise AssertionError("the limiter never refused")


def test_a_rate_limited_send_does_not_strand_its_turn_id(client, free_user):
    hdr = {**free_user["headers"], **HDR}
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    _exhaust_rate_limit(client, free_user)
    with patch(STREAM_ROUTE, _stream_of(text)):
        refused = client.post("/v1/chat", json=_body("t-429"), headers=hdr)
        assert refused.status_code == 429
        assert chat_turns.running_info(free_user["user_id"], "t-429") is None

        client.app.state.rate_limiter._buckets.clear()
        again = client.post("/v1/chat", json=_body("t-429"), headers=hdr)
    assert again.status_code == 200
    assert again.headers["content-type"].startswith("text/event-stream"), again.text[:200]
    assert "event: envelope" in again.text


def test_answering_turn_in_progress_does_not_release_the_running_turn(client, free_user):
    hdr = {**free_user["headers"], **HDR}
    client.app.state.rate_limiter._buckets.clear()
    # Another request is running this id right now.
    assert chat_turns.begin(free_user["user_id"], "t-running")
    try:
        for _ in range(2):   # twice: the first answer must not have released it
            r = client.post("/v1/chat", json=_body("t-running"), headers=hdr)
            assert r.status_code == 200 and r.json()["type"] == "turn_in_progress"
        assert chat_turns.running_info(free_user["user_id"], "t-running") is not None
    finally:
        chat_turns.abandon(free_user["user_id"], "t-running")


def test_a_streamed_turn_holds_its_id_while_it_runs_and_replays_after(client, free_user):
    hdr = {**free_user["headers"], **HDR}
    client.app.state.rate_limiter._buckets.clear()
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    seen = {}

    async def gen(provider_router, request, db, settings):
        # Runs INSIDE the stream's task, after the route wrapper has already
        # returned the StreamingResponse. The id must still be held here.
        seen["held"] = chat_turns.running_info(free_user["user_id"], "t-stream") is not None
        async for ev in _stream_of(text)(provider_router, request, db, settings):
            yield ev

    with patch(STREAM_ROUTE, gen):
        first = client.post("/v1/chat", json=_body("t-stream"), headers=hdr)
    assert "event: envelope" in first.text
    assert seen["held"] is True, "the wrapper released a turn that was still running"

    with patch(STREAM_ROUTE, _stream_of(text)):
        again = client.post("/v1/chat", json=_body("t-stream"), headers=hdr)
    assert again.headers["content-type"].startswith("application/json")
    assert again.json()["replayed"] is True and "stream" not in again.json()


def test_a_finished_json_turn_still_replays(client, free_user):
    """The wrapper's release runs after a finished turn too. It must be a
    no-op there and never un-store the result."""
    from app.models.chat import ChatResponse
    hdr = {**free_user["headers"], **HDR}
    client.app.state.rate_limiter._buckets.clear()
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    calls = []

    async def fake(provider_router, request, db, settings):
        calls.append(1)
        return ChatResponse(text=text, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                            provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        a = client.post("/v1/chat", json=_body("t-json", stream=False), headers=hdr)
        b = client.post("/v1/chat", json=_body("t-json", stream=False), headers=hdr)
    assert a.status_code == b.status_code == 200
    assert "replayed" not in a.json() and b.json()["replayed"] is True
    assert len(calls) == 1, "the resend reached the provider a second time"
