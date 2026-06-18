"""Streaming /v1/chat emits progress heartbeats during a silent gap.

Part 2 of the honest-progress hybrid: while the upstream is silent before
the first token (model thinking / queued / running web_search), GP emits
`{"type":"progress","phase":"waiting","elapsed_ms":...}` every heartbeat
window so a client can keep an honest "still working" indicator alive —
phase only, no fabricated completion fraction. The heartbeat must NOT
cancel the in-flight read.
"""

import asyncio
import json
from unittest.mock import patch

from app.models.chat import ChatResponse
from tests.conftest import chat_request


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_stream_emits_progress_heartbeat_during_silent_gap(
    client_with_cq, pro_user, monkeypatch
):
    from app.routers import chat as chat_module

    # Fire heartbeats almost immediately so the test stays fast.
    monkeypatch.setattr(chat_module, "_STREAM_HEARTBEAT_SECONDS", 0.05)

    canned = ChatResponse(
        text="Hello", input_tokens=100, output_tokens=50,
        model="claude-haiku-4-5-20251001", provider="anthropic",
        usage={"input_tokens": 100, "output_tokens": 50},
    )

    async def fake_stream(_body):
        # Silent for several heartbeat windows, then the first token, then done.
        await asyncio.sleep(0.25)
        yield {"type": "text", "text": "Hello", "done": False}
        yield {"type": "text", "text": "", "done": True, "response": canned}

    with patch(
        "app.services.provider_router.ProviderRouter.route_stream",
        side_effect=lambda body: fake_stream(body),
    ):
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(stream=True, call_type="query"),
            headers=pro_user["headers"],
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e.get("type") for e in events]

    progress = [e for e in events if e.get("type") == "progress"]
    assert progress, f"no heartbeat emitted: {types}"
    # Pre-first-token heartbeat: phase=waiting, elapsed only, no fraction.
    assert progress[0]["phase"] == "waiting"
    assert "elapsed_ms" in progress[0]
    assert "fraction" not in progress[0]
    assert "percent" not in progress[0]

    # Heartbeat precedes the first text, which precedes the terminal done.
    assert types.index("progress") < types.index("text")
    assert any(e.get("type") == "text" and e.get("text") == "Hello" for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_fast_stream_emits_no_heartbeat(client_with_cq, pro_user, monkeypatch):
    """When tokens arrive faster than the heartbeat window, the wire is
    unchanged from before — no progress events, just text + done."""
    from app.routers import chat as chat_module

    monkeypatch.setattr(chat_module, "_STREAM_HEARTBEAT_SECONDS", 5.0)

    canned = ChatResponse(
        text="Hi", input_tokens=10, output_tokens=5,
        model="claude-haiku-4-5-20251001", provider="anthropic",
        usage={"input_tokens": 10, "output_tokens": 5},
    )

    async def fast_stream(_body):
        yield {"type": "text", "text": "Hi", "done": False}
        yield {"type": "text", "text": "", "done": True, "response": canned}

    with patch(
        "app.services.provider_router.ProviderRouter.route_stream",
        side_effect=lambda body: fast_stream(body),
    ):
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(stream=True, call_type="query"),
            headers=pro_user["headers"],
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert not [e for e in events if e.get("type") == "progress"]
    assert any(e.get("type") == "done" for e in events)
