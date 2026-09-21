"""Capture the interviewer lane's sentence stream as RAW BYTES, for the client.

    .venv/bin/python -m pytest tests/capture_n400_stream_fixtures.py -q -p no:cacheprovider

Not collected by a normal run (the name does not start with test_). It drives
the REAL route with the route tests' mocked provider and writes exactly what
left the server to `qa/fixtures/n400-stream/`, so the N-400 client can build
and pin its SSE reader offline at zero cost. The provider is fake; every byte
of framing, the guard pipeline and the envelope are the production code path.

It also writes what the dedupe block answers when a streamed turn is re-sent
with the same top level `turn_id`, because nothing else in the suite sends a
stream and a `turn_id` together.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from tests.test_n400_sentence_stream_route import AGENDA, ENVELOPE, _metadata, _stream_of

OUT = Path(__file__).resolve().parent.parent / "qa" / "fixtures" / "n400-stream"
ROUTE = "app.services.anthropic_or_fallback.route_stream_with_fallback"


def _raw(client, free_user, gen, **body_over) -> tuple[bytes, dict]:
    from tests.conftest import chat_request
    body = chat_request(system_prompt="", user_content="on my own, about six years",
                        stream=True, metadata=_metadata())
    body.update(body_over)
    # The free tier's per minute limit is lower than the number of captures,
    # and a 429 here is a capture of the limiter, not of the stream.
    client.app.state.rate_limiter._buckets.clear()
    with patch(ROUTE, gen):
        with client.stream("POST", "/v1/chat", json=body,
                           headers={**free_user["headers"], "X-App-ID": "n400"}) as r:
            return r.read(), {"status": r.status_code,
                              "content_type": r.headers.get("content-type")}


def _write(name: str, raw: bytes, meta: dict, note: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(raw)
    (OUT / (name + ".meta.json")).write_text(json.dumps({**meta, "note": note}, indent=1))


def test_capture(client, free_user):
    text = json.dumps(ENVELOPE, ensure_ascii=False)

    raw, meta = _raw(client, free_user, _stream_of(text))
    _write("01-ordinary.sse", raw, meta,
           "Two sentence events, then the envelope holding the third as stream.held_sentence. "
           "The guard dropped a stale `asking`, so the envelope text differs from the model text.")

    order = ["schema_version", "turn_id", "reply", "intent", "facts", "deferred",
             "clarification", "conflict", "escalation", "complete", "asking",
             "section_checkpoint", "interview_over"]
    raw, meta = _raw(client, free_user,
                     _stream_of(json.dumps({k: ENVELOPE[k] for k in order}, ensure_ascii=False)))
    _write("02-buffered-reply-before-keys.sse", raw, meta,
           "The model wrote reply before facts/deferred/section_checkpoint. Nothing streams; "
           "one envelope, held_sentence null, buffered names the reason.")

    oath = AGENDA + "q_p9_oath | Part 9: Oath | p9.willing_bear_arms | Are you willing to bear arms?\n"
    raw, meta = _raw(client, free_user, _stream_of(text), user_content="yes",
                     metadata=_metadata(agenda=oath))
    _write("03-buffered-oath.sse", raw, meta,
           "An oath node is on the agenda, so the whole turn is buffered.")

    cut = text.index("Now, what is")

    async def dies(provider_router, request, db, settings):
        for i in range(0, cut, 7):
            yield {"type": "text", "text": text[i:i + 7]}
        raise RuntimeError("upstream died mid-stream")
    raw, meta = _raw(client, free_user, dies)
    _write("04-error-after-release.sse", raw, meta,
           "Two sentences released, then the provider died. One error event, no envelope, "
           "sentences_released = 2. HTTP status is still 200: the failure is IN the stream.")

    async def slow(provider_router, request, db, settings):
        await asyncio.sleep(0.35)
        async for ev in _stream_of(text)(provider_router, request, db, settings):
            yield ev
    with patch("app.routers.chat._PROGRESS_TICK_SECONDS", 0.1):
        raw, meta = _raw(client, free_user, slow)
    _write("05-progress-then-ordinary.sse", raw, meta,
           "Progress frames before the first sentence. The tick was shortened from 5s to 0.1s "
           "to capture them, so elapsed_seconds reads 0 here; in production it is 5, 10, 15...")

    es = dict(ENVELOPE, reply={"es": "Seis años por su cuenta, anotado. Ese es el camino general de cinco años. "
                                     "Ahora, ¿cuál es su número A?"})
    raw, meta = _raw(client, free_user, _stream_of(json.dumps(es, ensure_ascii=False)),
                     metadata=_metadata(locale="es"))
    _write("06-spanish-non-ascii.sse", raw, meta,
           "Non ASCII is sent as UTF-8, never \\u escaped (ensure_ascii=False), and the "
           "provider deltas here split multi byte characters across chunks.")

    # The same top level turn_id twice. The first is a stream; what is the second?
    raw, meta = _raw(client, free_user, _stream_of(text), turn_id="t-dedupe-1")
    _write("07a-first-send-with-turn-id.sse", raw, meta, "First send, top level turn_id set.")
    raw, meta = _raw(client, free_user, _stream_of(text), turn_id="t-dedupe-1")
    _write("07b-resend-after-it-finished.json", raw, meta,
           "The SAME request again after the turn FINISHED, stream still true. Plain JSON, "
           "not a stream: the stored body plus replayed=true, and NO `stream` object.")

    # A stream that died after two sentences, then the same id again.
    raw, meta = _raw(client, free_user, dies, turn_id="t-dedupe-died")
    _write("08a-died-after-release-with-turn-id.sse", raw, meta, "Provider died mid stream.")
    raw, meta = _raw(client, free_user, _stream_of(text), turn_id="t-dedupe-died")
    _write("08b-resend-after-it-died.body", raw, meta,
           "The same id after the stream died. Read content_type to see what came back.")

    # A send the rate limiter refused BEFORE any stream began, then the same id.
    from tests.conftest import chat_request
    body = chat_request(system_prompt="", user_content="on my own, about six years",
                        stream=True, metadata=_metadata(), turn_id="t-dedupe-429")
    client.app.state.rate_limiter._buckets.clear()
    hdr = {**free_user["headers"], "X-App-ID": "n400"}
    last = None
    with patch(ROUTE, _stream_of(text)):
        for i in range(40):
            last = client.post("/v1/chat", json={**body, "turn_id": f"t-burn-{i}"}, headers=hdr)
            if last.status_code == 429:
                break
        refused = client.post("/v1/chat", json=body, headers=hdr)
        _write("09a-rate-limited-before-the-stream.json", refused.content,
               {"status": refused.status_code, "content_type": refused.headers.get("content-type"),
                "retry_after_header": refused.headers.get("retry-after")},
               "A refusal BEFORE the stream starts is a plain JSON HTTP error, never an SSE error event.")
        client.app.state.rate_limiter._buckets.clear()
        again = client.post("/v1/chat", json=body, headers=hdr)
        _write("09b-resend-after-the-429.body", again.content,
               {"status": again.status_code, "content_type": again.headers.get("content-type")},
               "The same id right after the 429, with the limiter cleared.")
