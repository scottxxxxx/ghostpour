"""A retry costs a lookup, not a second model call (2026-08-30).

On 2026-08-29 Scott asked one question with two documents attached and GP
built the full answer THREE times, at $0.2738, $0.2623 and $0.2633, and
delivered none of them. Each attempt re-uploaded 400,653 bytes and re-ran an
identical 10,798-token prompt, because nothing could say "you already asked
me this and I already answered it".

The load-bearing assertion in this file is `mock_provider.await_count`. Every
other check here is about shape; that one is about money, and it is the only
one that would have caught the actual defect.
"""

from __future__ import annotations

import uuid

from tests.conftest import chat_request


def _turn(turn_id, stream=False):
    return chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="What did we decide about the pricing tiers?",
        stream=stream, turn_id=turn_id,
        metadata={"prompt_mode": "ProjectChat"},
    )


def _h(user):
    return {**user["headers"], "X-App-ID": "shouldersurf", "X-App-Build": "1330"}


def test_a_resent_turn_id_does_not_run_the_model_again(
        client, free_user, mock_provider):
    """THE claim. Everything else in this file is shape."""
    tid = str(uuid.uuid4())

    first = client.post("/v1/chat", json=_turn(tid), headers=_h(free_user))
    assert first.status_code == 200
    calls_after_first = mock_provider.await_count
    assert calls_after_first >= 1

    second = client.post("/v1/chat", json=_turn(tid), headers=_h(free_user))
    assert second.status_code == 200

    # The whole point: not one additional upstream call.
    assert mock_provider.await_count == calls_after_first

    assert second.json()["text"] == first.json()["text"]
    assert second.json()["replayed"] is True


def test_the_replayed_body_is_the_whole_body_not_just_the_text(
        client, free_user, mock_provider):
    """The client parses a replay with the same branch as the original, so a
    stored summary would silently drop fields and only show up as a subtly
    wrong UI on the retry path, which is the path nobody looks at."""
    tid = str(uuid.uuid4())
    live = client.post("/v1/chat", json=_turn(tid), headers=_h(free_user)).json()

    # Read what was STORED, not what a second identical run produced. The
    # first version of this test compared two live responses, and the mock
    # provider returns the same canned answer every time, so it passed even
    # with the replay disabled entirely: a test that could not fail on the
    # bug it was written for. Caught by sabotage, 2026-08-30.
    stored = client.get(f"/v1/chat/turns/{tid}", headers=_h(free_user)).json()["body"]

    missing = set(live) - set(stored)
    assert not missing, f"dropped from the stored body: {missing}"
    for k, v in live.items():
        assert stored[k] == v, k


def test_a_turn_without_an_id_still_runs_every_time(
        client, free_user, mock_provider):
    """Absent turn_id is today's behaviour exactly, so every shipped build is
    unaffected and this is additive on the wire."""
    body = _turn(None)
    body.pop("turn_id")
    client.post("/v1/chat", json=body, headers=_h(free_user))
    n = mock_provider.await_count
    client.post("/v1/chat", json=body, headers=_h(free_user))
    assert mock_provider.await_count > n


def test_a_stored_turn_is_recoverable_by_lookup(
        client, free_user, mock_provider):
    tid = str(uuid.uuid4())
    sent = client.post("/v1/chat", json=_turn(tid), headers=_h(free_user)).json()

    got = client.get(f"/v1/chat/turns/{tid}", headers=_h(free_user))
    assert got.status_code == 200
    assert got.json()["status"] == "done"
    assert got.json()["body"]["text"] == sent["text"]


def test_an_unknown_turn_is_404_and_therefore_terminal(client, free_user):
    """Never-existed, expired, not-yours and lost-to-restart are one
    indistinguishable 404, because the client's answer to all four is the
    same: resend in full. It is also the cheap case, since a turn we have no
    record of never reached an upstream call."""
    r = client.get(f"/v1/chat/turns/{uuid.uuid4()}", headers=_h(free_user))
    assert r.status_code == 404


def test_another_users_turn_is_not_readable(
        client, free_user, pro_user, mock_provider):
    """Owner-only, and it must 404 rather than 403: a distinguishable refusal
    would confirm the id exists."""
    tid = str(uuid.uuid4())
    client.post("/v1/chat", json=_turn(tid), headers=_h(free_user))

    r = client.get(f"/v1/chat/turns/{tid}", headers=_h(pro_user))
    assert r.status_code == 404


def test_another_users_turn_id_does_not_replay_into_their_answer(
        client, free_user, pro_user, mock_provider):
    """The key is (user_id, turn_id). A colliding id from another account must
    run its own turn, never hand over the first user's answer."""
    tid = str(uuid.uuid4())
    client.post("/v1/chat", json=_turn(tid), headers=_h(free_user))
    n = mock_provider.await_count

    r = client.post("/v1/chat", json=_turn(tid), headers=_h(pro_user))
    assert r.status_code == 200
    assert mock_provider.await_count > n, "a foreign id must not serve a replay"
    assert r.json().get("replayed") is not True


def test_an_in_flight_turn_is_answered_not_re_run(
        client, free_user, mock_provider):
    """Answering rather than attaching is the point: holding the request open
    is the long silent socket that started all of this."""
    from app.services import chat_turns
    tid = str(uuid.uuid4())
    assert chat_turns.begin(free_user["user_id"], tid) is True
    try:
        n = mock_provider.await_count
        r = client.post("/v1/chat", json=_turn(tid), headers=_h(free_user))
        assert r.status_code == 200
        assert r.json()["type"] == "turn_in_progress"
        assert mock_provider.await_count == n
    finally:
        chat_turns.abandon(free_user["user_id"], tid)


def test_in_progress_reports_elapsed_and_predicts_nothing():
    """Unlike the generation lane, no expected_seconds. We hold a measured
    expectation for an artifact build and none for a chat turn, and a number
    we cannot stand behind is the fabrication this lane began as."""
    from app.services import chat_turns
    tid = str(uuid.uuid4())
    chat_turns.begin("unit-test-user", tid)
    try:
        info = chat_turns.running_info("unit-test-user", tid)
        assert info["status"] == "in_progress"
        assert "elapsed_seconds" in info
        assert "expected_seconds" not in info
    finally:
        chat_turns.abandon("unit-test-user", tid)


def test_a_stale_in_flight_entry_heals_instead_of_stranding_the_id():
    """The leak valve. The handler has many early-return paths, and a rule
    that every future one must remember to clean up is the kind that fails.
    Without this a stranded id reads `in_progress` forever and the client
    polls a turn that no longer exists."""
    from datetime import timedelta

    from app.services import chat_turns
    tid = str(uuid.uuid4())
    chat_turns.begin("unit-test-user", tid)
    entry = chat_turns._IN_FLIGHT[("unit-test-user", tid)]
    entry["started_at"] -= timedelta(seconds=chat_turns._IN_FLIGHT_MAX_SECONDS + 5)

    assert chat_turns.running_info("unit-test-user", tid) is None
    # and it can be claimed again rather than being wedged
    assert chat_turns.begin("unit-test-user", tid) is True
    chat_turns.abandon("unit-test-user", tid)
