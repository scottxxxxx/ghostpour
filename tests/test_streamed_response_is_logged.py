"""A streamed call must record what a non-streamed one records.

It did not. `send_request_stream` ended with `raw_response_json=None`,
commented "Not available in streaming", and that was wrong twice over:
the stream had already accumulated the text and the stop reason, and we
threw both away at the last line.

Measured 2026-08-20: 210 of 1668 calls over 60 days had NO stored
response, concentrated in the busiest lanes (ShoulderSurf `query` 96%,
`meeting_chat` 83%, `meeting_chat_follow_up` 71%, `tr_brief_analysis`
46% since it began streaming).

Why it matters more than a gap in a log: `stop_reason` is the field
every truncation question turns on. A sweep counting
`stop_reason == "max_tokens"` cannot distinguish "did not truncate"
from "we did not look", and on those rows it could not look. It
reported zero truncations, which is what "we did not look" looks like.
That claim was made about `tr_brief_analysis` specifically, and it was
made to a partner team.
"""

import asyncio
import json
from unittest.mock import patch

from app.models.chat import ChatRequest
from app.services.providers.anthropic import AnthropicAdapter


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(
        api_key="test",
        base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key",
        auth_prefix="",
    )


def _stream(sse_lines, request=None):
    async def fake_post_stream(_self, _url, _body, _headers):
        for line in sse_lines:
            yield line

    req = request or ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="sys", user_content="hi", stream=True,
    )

    async def _drain():
        events = []
        with patch.object(AnthropicAdapter, "_post_stream", fake_post_stream):
            async for ev in _adapter().send_request_stream(req):
                events.append(ev)
        return events

    events = asyncio.run(_drain())
    done = [e for e in events if e.get("done")]
    assert done, f"stream produced no done event: {events!r}"
    return done[-1]["response"]


def _lines(stop_reason, text="answer", thinking=None):
    out = [
        'data: {"type":"message_start","message":{"id":"msg_1",'
        '"model":"claude-sonnet-4-6","usage":{"input_tokens":11}}}',
    ]
    if thinking is not None:
        out.append(
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":%s}}' % json.dumps(thinking))
    out += [
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":%s}}' % json.dumps(text),
        'data: {"type":"message_delta","delta":{"stop_reason":%s},'
        '"usage":{"output_tokens":42}}' % json.dumps(stop_reason),
        'data: {"type":"message_stop"}',
    ]
    return out


def test_a_streamed_call_records_a_response_at_all():
    resp = _stream(_lines("end_turn"))
    assert resp.raw_response_json is not None, (
        "a streamed call recorded nothing, so no question about this call "
        "can ever be answered from the record")
    json.loads(resp.raw_response_json)


def test_the_stop_reason_survives():
    """The whole point. Without this the truncation sweep is blind."""
    resp = _stream(_lines("end_turn"))
    assert json.loads(resp.raw_response_json)["stop_reason"] == "end_turn"


def test_a_TRUNCATED_stream_is_visibly_truncated_afterwards():
    """The case the blind spot was hiding: this is what a cut-off response
    looks like, and until now it looked identical to a clean one."""
    resp = _stream(_lines("max_tokens"))
    assert json.loads(resp.raw_response_json)["stop_reason"] == "max_tokens"


def test_it_reads_like_a_non_streamed_body_so_existing_readers_work():
    """Every consumer of raw_response was written against the non-streamed
    shape. A differently-shaped body would be recorded and still unreadable."""
    doc = json.loads(_stream(_lines("end_turn", text="hello")).raw_response_json)
    assert doc["type"] == "message"
    assert doc["role"] == "assistant"
    assert doc["content"] == [{"type": "text", "text": "hello"}]
    assert "usage" in doc and "stop_reason" in doc
    # the shape existing code walks to pull the text back out
    assert "".join(b["text"] for b in doc["content"] if b["type"] == "text") == "hello"


def test_it_says_it_was_reconstructed_rather_than_posing_as_verbatim():
    """A reconstructed body that looks like the provider's own bytes is a
    stub wearing the clothes of a record. Anyone auditing this later must
    be able to tell which calls were assembled by us."""
    doc = json.loads(_stream(_lines("end_turn")).raw_response_json)
    assert doc["_gp_reconstructed_from_stream"] is True


def test_omitted_thinking_is_declared_not_silently_dropped():
    """Thinking deltas are counted, never accumulated, so output_tokens can
    exceed what the recorded text accounts for. That discrepancy is real
    and the record says so, rather than leaving someone to rediscover it
    as a contradiction."""
    doc = json.loads(_stream(_lines("end_turn", thinking="pondering")).raw_response_json)
    assert doc["_gp_thinking_chars_omitted"] == len("pondering")
    assert doc["content"] == [{"type": "text", "text": "answer"}], (
        "thinking text must not be folded into the visible content")
    no_thinking = json.loads(_stream(_lines("end_turn")).raw_response_json)
    assert no_thinking["_gp_thinking_chars_omitted"] == 0


def test_a_stream_that_never_reports_a_stop_reason_records_null_not_empty():
    """An absent stop reason must be null, the same as a non-streamed body
    that lacks one, rather than an empty string that compares unequal to
    every real value and equal to none of them."""
    lines = [
        'data: {"type":"message_start","message":{"id":"m","model":"x",'
        '"usage":{"input_tokens":1}}}',
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"partial"}}',
    ]
    doc = json.loads(_stream(lines).raw_response_json)
    assert doc["stop_reason"] is None
