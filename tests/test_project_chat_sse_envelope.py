"""Project Chat must not sit silent for a minute (2026-08-29).

`should_stream` excludes Project Chat on purpose, and that exclusion is what
kept it immune to the 07-13 bug where a confirmed Gantt turn token-streamed
raw task-graph JSON into the bubble and built no file. The exclusion also took
it off the only path that emits heartbeats, so a Project Chat turn sent
nothing at all, not one byte and not even HTTP response headers, until the whole
JSON was built.

Measured on Scott's device that day: three turns carrying 400,653 bytes of
documents ran 66.4s, 56.3s and 56.7s, completed, cost $0.7995, and none
reached the phone. The client's own log shows the foreground one died after
36s with `request_id=none`, i.e. no response headers ever arrived.

So the transport changes and the payload does not. These tests pin both
halves, plus the trap in the middle: the client sets `sawGenerationEvents` on
ANY `generation_*` event, and that flag now gates its "your file is still
being built" copy, so a plain chat turn must never emit one before the end.
"""

import json as _json

import pytest

from tests.conftest import chat_request


# 803 is the App Store build SS verified line by line; the floor is 695.
BUILD_OK = 803
UA_BELOW = "Shoulder%20Surf/694 CFNetwork/3860.700.1 Darwin/25.6.0"


def _project_chat(stream: bool, **meta):
    return chat_request(
        prompt_mode="ProjectChat",
        call_type="query",
        user_content="What did we decide about the pricing tiers?",
        stream=stream,
        metadata={"prompt_mode": "ProjectChat", **meta},
    )


def _headers(user, *, build=BUILD_OK, ua=None):
    h = {**user["headers"], "X-App-ID": "shouldersurf"}
    if build is not None:
        h["X-App-Build"] = str(build)
    if ua is not None:
        h["User-Agent"] = ua
    return h


def test_streaming_project_chat_answers_as_sse(client, free_user, mock_provider):
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user))
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: generation_result" in r.text


def test_the_delivered_body_is_the_json_body_unchanged(
        client, free_user, mock_provider):
    """The whole point: transport moves, payload does not."""
    plain = client.post("/v1/chat", json=_project_chat(stream=False),
                        headers=_headers(free_user))
    assert plain.status_code == 200
    assert "application/json" in plain.headers["content-type"]

    streamed = client.post("/v1/chat", json=_project_chat(stream=True),
                           headers=_headers(free_user))
    assert streamed.status_code == 200
    delivered = _json.loads(
        streamed.text.split("event: generation_result\ndata: ")[1].split("\n")[0])

    assert delivered["text"] == plain.json()["text"]
    # Every key the client reads off a non-stream body must survive the move.
    for key in ("text", "input_tokens", "output_tokens"):
        assert key in delivered, key


def test_a_chat_turn_never_arms_the_generation_family(
        client, free_user, mock_provider):
    """`generation_started` / `generation_progress` set `sawGenerationEvents`
    on the client, which gates its "your file is still being built" copy. A
    plain chat turn promising a build is the exact defect Scott hit."""
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user))
    # Anchor first: without this the assertions below pass vacuously on any
    # change that puts the turn back on the silent JSON path.
    assert "event: generation_result" in r.text
    assert "event: generation_started" not in r.text
    assert "event: generation_progress" not in r.text


def test_heartbeats_arrive_while_the_turn_is_still_running(
        client, free_user, mock_provider, monkeypatch):
    """The whole incident is 'no bytes for 60 seconds'. Prove bytes flow
    BEFORE the answer is ready, not merely that the answer arrives."""
    import asyncio

    import app.routers.chat as chat_mod
    monkeypatch.setattr(chat_mod, "_PROGRESS_TICK_SECONDS", 0.02)

    canned = mock_provider.canned_response

    async def _slow(*a, **kw):
        await asyncio.sleep(0.3)
        return canned

    mock_provider.side_effect = _slow

    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user))
    assert r.status_code == 200
    assert "event: progress" in r.text
    # Ordering is the claim: liveness reached the client before the answer.
    assert r.text.index("event: progress") < r.text.index("event: generation_result")


def test_unstreamed_project_chat_still_answers_as_json(
        client, free_user, mock_provider):
    """A client that did not ask to stream keeps today's behaviour exactly."""
    r = client.post("/v1/chat", json=_project_chat(stream=False),
                    headers=_headers(free_user))
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    assert "event: generation_result" not in r.text


def test_non_project_chat_is_untouched_by_the_envelope(
        client, free_user, mock_provider):
    """Meeting chat has its own streaming path; this change must not divert
    it onto the buffered envelope."""
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="MeetingChat", call_type="query",
        user_content="What did we decide?", stream=True,
    ), headers=_headers(free_user))
    assert r.status_code == 200
    assert "event: generation_result" not in r.text


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Token abc"}])
def test_missing_or_non_bearer_auth_is_401_not_403(client, headers):
    """SS's rescue lookup treats anything that is not 200 or 404 as
    'unparseable', so FastAPI's default 403 for a missing header bought Scott
    90 minutes of polling that could never succeed. Every other auth failure
    in `get_current_user` is already a 401."""
    r = client.get("/v1/generations/does-not-exist", headers=headers)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# The build floor. The repo can name the build that understands the envelope;
# it cannot say what is installed. Below 695 the envelope arrives as a stream
# carrying no text events and renders an EMPTY answer, which is worse than the
# silence it replaces, so this gate fails CLOSED on a build it cannot read.


def test_a_build_below_the_floor_keeps_the_json_path(
        client, free_user, mock_provider):
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user, build=694))
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    assert "event: generation_result" not in r.text


def test_the_floor_build_itself_gets_the_envelope(
        client, free_user, mock_provider):
    """695 is the floor, so 695 is included. An off-by-one here silently
    strands the exact build SS verified."""
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user, build=695))
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: generation_result" in r.text


def test_an_unreadable_build_fails_closed(client, free_user, mock_provider):
    """No X-App-Build and no readable UA token. Every other gate in
    version_gate allows here; this one must not."""
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user, build=None,
                                     ua="python-httpx/0.27"))
    assert "application/json" in r.headers["content-type"]
    assert "event: generation_result" not in r.text


def test_the_build_can_be_read_from_the_user_agent_alone(
        client, free_user, mock_provider):
    """Builds before 648 sent no X-App-Build, so the UA token is the only
    reading. Pinned so a UA-only old build is refused for the right reason
    rather than passing as unreadable."""
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user, build=None, ua=UA_BELOW))
    assert "application/json" in r.headers["content-type"]


def test_a_ua_only_build_above_the_floor_STILL_gets_the_envelope(
        client, free_user, mock_provider):
    """The gap that made a wrong conclusion plausible (2026-08-30).

    SS found that the chat stream builds its own URLSessionConfiguration and
    sets only X-App-ID, so X-App-Build is absent on that socket, and read that
    as "the gate refuses every device". It does not: `build_number` falls back
    to the default URLSession User-Agent's leading token, which is why that
    fallback exists at all. Measured at the edge the same night, 630 of 793
    POST /v1/chat requests carried a readable `Shoulder%20Surf/<build>` token
    and the 163 without one were Tech Rehearsal, curl and python-urllib.

    The old UA test only pinned the BELOW-floor direction, so nothing here
    proved the gate OPENS on a UA-only read. That asymmetry is the whole
    reason the claim survived as long as it did.
    """
    ua = "Shoulder%20Surf/803 CFNetwork/3860.700.1 Darwin/25.6.0"
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user, build=None, ua=ua))
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: generation_result" in r.text

def test_the_response_head_flushes_before_the_answer_is_ready(
        client, free_user, mock_provider):
    """The socket must get a byte immediately, not at the first tick.

    Measured on Scott's device 2026-08-30, turn 4b5ef89ad55f: the phone saw
    no headers until +10.34s. Server side, the first 5s tick was written at
    03:46:21.120 and the device observed headers at 03:46:21.140, a 20 ms
    gap, which proves the response head is flushed WITH the first body chunk
    rather than before it. Half that window was pure waiting.

    The mock provider returns instantly, so the turn completes before any
    tick could fire. Without the immediate frame there is no `progress` frame
    on this turn at all, which is exactly the silent-socket shape.
    """
    r = client.post("/v1/chat", json=_project_chat(stream=True),
                    headers=_headers(free_user))
    assert r.status_code == 200
    assert r.text.startswith("event: progress"), r.text[:120]
    assert r.text.index("event: progress") < r.text.index("event: generation_result")
