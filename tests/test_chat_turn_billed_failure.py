"""A failure is only worth storing if it cost money.

#823 stored EVERY terminal failure, so once a turn was recorded failed, every
subsequent send of that id replayed it instantly and forever without reaching
a model. That is correct for "do not rebuild work we already did" and wrong
for a retry button: a client reusing the id, which is exactly what the design
tells it to do, gets an affordance guaranteed to fail. And the failure most
likely to be replayed forever is a transient upstream one, which is precisely
the kind a retry fixes.

SS found it by reading the source, absorbed it client-side with an `isSpent`
rule, and asked for it to be a shared decision rather than a client
workaround. They proposed a list of transient-versus-terminal error codes.
The billed axis is better and is what shipped: it is the property "do not
rebuild work" is actually about, and it needs no list of error strings to
agree across two codebases, which is the misnaming half of the typed-hop
class and the half neither team has an instrument for.

`retryable` falls out of the SAME fact rather than a second list. Abandoned
means a retry re-runs and may differ; stored means a retry replays this and
cannot.

Note what the pre-existing suite did NOT cover: `test_chat_turn_idempotency`
has ten tests and not one exercises a failure. That is why this shipped.
"""

from __future__ import annotations

import uuid

import aiosqlite
import pytest

from app.services import chat_turns
from tests.conftest import chat_request


def _turn(turn_id):
    return chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="What did we decide about the pricing tiers?",
        turn_id=turn_id, metadata={"prompt_mode": "ProjectChat"},
    )


def _h(user):
    return {**user["headers"], "X-App-ID": "shouldersurf", "X-App-Build": "1330"}


# --- the flag itself ---

def test_the_flag_defaults_to_unbilled():
    """Anything that fails before reaching a provider must read as unbilled
    without having to say so. Default False is what makes abandon the
    behaviour you get by omission rather than by remembering."""
    assert chat_turns.upstream_was_billed() is False


@pytest.mark.asyncio
async def test_the_flag_does_not_leak_between_requests():
    """Each request runs in its own asyncio Task and contextvars are copied
    at task creation, so a billed turn must not make the NEXT turn look
    billed. If it leaked, one paid turn would poison every later failure
    into being stored and un-retryable."""
    import asyncio

    async def billed() -> bool:
        chat_turns.mark_upstream_billed()
        return chat_turns.upstream_was_billed()

    async def sibling() -> bool:
        return chat_turns.upstream_was_billed()

    assert await asyncio.create_task(billed()) is True
    assert await asyncio.create_task(sibling()) is False, \
        "a billed turn leaked into a sibling request"


# --- the decision ---

def test_an_UNBILLED_failure_is_abandoned_so_a_retry_re_runs(
    client, free_user, mock_provider, monkeypatch
):
    """The bug, from the client's side. A gate raising before any upstream
    call must leave the id UNKNOWN, so resending it runs the turn rather
    than replaying a failure that never cost anything."""
    from fastapi import HTTPException

    tid = str(uuid.uuid4())
    calls = {"n": 0}

    async def _boom(*a, **kw):
        calls["n"] += 1
        raise HTTPException(status_code=503, detail={
            "code": "upstream_error", "message": "provider unreachable"})

    monkeypatch.setattr(mock_provider, "side_effect", _boom, raising=False)
    first = client.post("/v1/chat", json=_turn(tid), headers=_h(free_user))
    assert first.status_code >= 400

    # The id must not have been recorded, so a resend RUNS rather than
    # replaying. That is the whole point of the billed axis.
    assert chat_turns.running_info(free_user["user_id"], tid) is None, \
        "the id was left in flight; a retry would poll in_progress"


def test_a_live_unbilled_error_says_it_is_retryable(
    client, free_user, mock_provider, monkeypatch
):
    """SS's second ask. Their ladder currently derives retryability from a
    code list on their side; a list that must agree across two codebases is
    the exact bug class we have no instrument for. GP holds the fact, so GP
    says it."""
    from fastapi import HTTPException

    async def _boom(*a, **kw):
        raise HTTPException(status_code=503, detail={
            "code": "upstream_error", "message": "provider unreachable"})

    monkeypatch.setattr(mock_provider, "side_effect", _boom, raising=False)
    resp = client.post("/v1/chat", json=_turn(str(uuid.uuid4())),
                       headers=_h(free_user))
    assert resp.status_code >= 400

    # The assertion that earns the test. An earlier version stopped at the
    # status code above and passed while `retryable` reached only the STORED
    # row and never the live response, which is the half SS cannot work
    # around: the live error is what their ladder reads.
    detail = resp.json().get("detail")
    assert isinstance(detail, dict), f"expected a dict detail, got {detail!r}"
    assert detail.get("retryable") is True, (
        "an unbilled failure must tell the client a retry can differ; "
        f"got {detail!r}"
    )


# --- the replay contract ---

@pytest.mark.asyncio
async def test_a_stored_failure_replays_as_NOT_retryable(client, free_user, tmp_db_path):
    """A stored failure is by definition one that cost money, so replaying
    it is the honest answer and a retry cannot differ. The field must be
    PRESENT rather than inferred."""
    tid = str(uuid.uuid4())
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await chat_turns.finish(
            db, user_id=free_user["user_id"], app_id="shouldersurf",
            turn_id=tid, status="failed",
            error={"code": "provider_error",
                   "message": "died after the model ran",
                   "http_status": 502, "retryable": False},
        )
        stored = await chat_turns.lookup_terminal(db, free_user["user_id"], tid)
    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_an_abandoned_id_reads_as_unknown(client, free_user, tmp_db_path):
    """Unknown is the correct answer for an unbilled failure: the client's
    response to unknown is a full resend, which is exactly what should
    happen when nothing was spent."""
    tid = str(uuid.uuid4())
    chat_turns.begin(free_user["user_id"], tid)
    chat_turns.abandon(free_user["user_id"], tid)

    assert chat_turns.running_info(free_user["user_id"], tid) is None
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        assert await chat_turns.lookup_terminal(
            db, free_user["user_id"], tid) is None
