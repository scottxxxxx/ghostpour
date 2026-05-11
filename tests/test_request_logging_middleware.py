"""Tests for StreamingBypassMiddleware — verifies that the "(streaming)" label
keys off the actual response content-type, not the request's `stream:true`
flag. Previously, a handler that ignored `stream:true` and returned JSON
(e.g. Project Chat) was logged with body "(streaming)" and no captured body.
"""

import json
import logging

import pytest

from app.middleware import request_logging
from app.middleware.request_logging import StreamingBypassMiddleware, _LOG_BUFFER


@pytest.fixture(autouse=True)
def _clear_buffer():
    _LOG_BUFFER.clear()
    yield
    _LOG_BUFFER.clear()


def _make_scope(path: str = "/v1/chat", body: bytes = b'{"stream":true}') -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json"), (b"x-app-id", b"test-app")],
        "_req_body": body,
    }


async def _drive(middleware: StreamingBypassMiddleware, scope: dict, handler):
    """Run the middleware once against `handler`, returning sent messages."""
    sent: list[dict] = []
    body = scope.pop("_req_body", b"")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    async def app(s, r, sd):
        await handler(s, r, sd)

    middleware.app = app
    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_json_response_to_stream_request_is_not_mislabeled():
    """Request has stream:true but handler returns JSON → log must NOT say (streaming),
    and the JSON body must be captured."""

    async def handler(scope, receive, send):
        body = json.dumps({"ok": True, "reply": "hello"}).encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

    mw = StreamingBypassMiddleware(app=None)
    await _drive(mw, _make_scope(body=b'{"stream":true,"q":"hi"}'), handler)

    assert len(_LOG_BUFFER) == 1
    entry = _LOG_BUFFER[0]
    assert entry["status"] == 200
    assert entry["response"]["body"] != "(streaming)"
    assert entry["response"]["body"] == {"ok": True, "reply": "hello"}


@pytest.mark.asyncio
async def test_sse_response_is_labeled_streaming_and_body_not_captured():
    """Actual SSE response → log says (streaming) and body is not captured,
    even though chunks still pass through to the client."""

    chunks = [b"data: hello\n\n", b"data: world\n\n", b"data: [DONE]\n\n"]

    async def handler(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        for c in chunks[:-1]:
            await send({"type": "http.response.body", "body": c, "more_body": True})
        await send({"type": "http.response.body", "body": chunks[-1], "more_body": False})

    mw = StreamingBypassMiddleware(app=None)
    sent = await _drive(mw, _make_scope(body=b'{"stream":true}'), handler)

    body_msgs = [m for m in sent if m["type"] == "http.response.body"]
    assert b"".join(m["body"] for m in body_msgs) == b"".join(chunks)

    assert len(_LOG_BUFFER) == 1
    entry = _LOG_BUFFER[0]
    assert entry["response"]["body"] == "(streaming)"


@pytest.mark.asyncio
async def test_streaming_label_emitted_to_logger(caplog):
    """The summary log line gets the `(streaming)` suffix only for actual SSE."""

    async def json_handler(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    mw = StreamingBypassMiddleware(app=None)
    with caplog.at_level(logging.INFO, logger="ghostpour"):
        await _drive(mw, _make_scope(body=b'{"stream":true}'), json_handler)
    assert not any("(streaming)" in r.getMessage() for r in caplog.records)

    _LOG_BUFFER.clear()
    caplog.clear()

    async def sse_handler(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        await send({"type": "http.response.body", "body": b"data: x\n\n", "more_body": False})

    with caplog.at_level(logging.INFO, logger="ghostpour"):
        await _drive(mw, _make_scope(body=b'{"stream":true}'), sse_handler)
    assert any("(streaming)" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_skip_paths_bypass_logging():
    """Paths in _SKIP_PATHS produce no buffer entry and no log line."""

    async def handler(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    mw = StreamingBypassMiddleware(app=None)
    await _drive(mw, _make_scope(path="/health"), handler)
    assert len(_LOG_BUFFER) == 0


@pytest.mark.asyncio
async def test_x_request_id_header_added_to_response():
    """The middleware injects x-request-id into the response headers."""

    async def handler(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    mw = StreamingBypassMiddleware(app=None)
    sent = await _drive(mw, _make_scope(), handler)
    start_msg = next(m for m in sent if m["type"] == "http.response.start")
    header_names = [h[0] for h in start_msg["headers"]]
    assert b"x-request-id" in header_names
