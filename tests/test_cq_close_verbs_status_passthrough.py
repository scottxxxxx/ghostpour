"""A 409 from CQ must reach the device as a 409 with its body intact.

SS is building state machine behaviour on exactly this distinction: a
409 means the row is closed, so refetch and render the closed state; a
network failure means the write never landed, so put the row back. Those
two need to stay tellable apart across our hop, or their fix becomes the
same bug in a new place.

The pair that matters is `complete` and `vouch`, because their confirm
card denies via vouch, and CQ measured on prod that vouch against an
already-closed row returns 409 with the same detail as a duplicate
complete. So both arms of that card depend on this.

These run the REAL `_cq_proxy` with only CQ's HTTP response stubbed, and
the interesting hole here is REQUEST side, so the middlebox modelled in
the sabotage mangles what SS sends rather than what CQ answers.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-close-1"
PATCH = "patch-abc"

# Verbatim from CQ's prod measurement 2026-08-19, not invented here.
CQ_409_BODY = {"detail": "Patch is already completed or archived"}
CQ_200_ECHO = {"status": "completed", "patch_id": PATCH,
               "completed_at": "2026-08-19T04:12:00.123456+00:00"}


def _user(user_id: str = USER) -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_close", tier="free",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def quilt_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def cq(monkeypatch):
    """Stub CQ's HTTP answer; expose the outbound call for request-side
    assertions. Returns a handle whose `.sent` is what we put on the wire
    to CQ."""
    monkeypatch.setattr(
        cq_proxy, "get_settings",
        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))

    handle = SimpleNamespace(instance=None, sent=None)

    def _install(payload, status=200, raises=None):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        resp.text = json.dumps(payload)
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        if raises is not None:
            instance.request = AsyncMock(side_effect=raises)
        else:
            instance.request = AsyncMock(return_value=resp)
        handle.instance = instance
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient",
                            lambda *a, **k: instance)
        return handle
    handle.install = _install
    return handle


def _outbound(handle):
    """(method, path, json_body) as it left us for CQ."""
    call = handle.instance.request.await_args
    return call.args[0], call.args[1], call.kwargs.get("json")


VERBS = ("complete", "vouch")


@pytest.mark.parametrize("verb", VERBS)
def test_a_409_arrives_as_a_409_with_the_body_intact(quilt_client, cq, verb):
    """The status AND the body. A 409 whose detail got flattened still
    tells SS the row is closed, but it stops telling them WHY, and the
    detail is the only thing distinguishing already-closed from the other
    409s CQ may add later."""
    cq.install(CQ_409_BODY, status=409)

    resp = quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/{verb}")

    assert resp.status_code == 409, (
        f"{verb}: SS reads 409 as 'closed, refetch and render closed'. "
        f"Any other status sends them down the retry arm instead.")
    assert json.loads(resp.content) == CQ_409_BODY, (
        f"{verb}: body did not survive the hop verbatim")


@pytest.mark.parametrize("verb", VERBS)
def test_a_409_is_tellable_apart_from_a_network_failure(quilt_client, cq, verb):
    """This is the whole distinction SS's state machine turns on. A
    write that was REFUSED (row already closed) and a write that never
    LANDED (transport died) must not arrive as the same thing, because
    the correct client behaviour is opposite: render the closed state
    versus put the row back."""
    import httpx as _httpx

    cq.install(CQ_409_BODY, status=409)
    refused = quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/{verb}")

    cq.install({}, raises=_httpx.TimeoutException("boom"))
    timed_out = quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/{verb}")

    cq.install({}, raises=RuntimeError("connection reset"))
    unreachable = quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/{verb}")

    assert refused.status_code == 409
    assert timed_out.status_code == 504
    assert unreachable.status_code == 502
    assert len({refused.status_code, timed_out.status_code,
                unreachable.status_code}) == 3, (
        "refused, timed out and unreachable collapsed into fewer than "
        "three answers, so the client cannot tell them apart")


def test_the_200_echo_on_complete_survives_verbatim(quilt_client, cq):
    """CQ is requiring SS to confirm the close by comparing this echo
    rather than trusting a 2xx, so every key in it is load bearing."""
    cq.install(CQ_200_ECHO, status=200)

    resp = quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/complete")

    assert resp.status_code == 200
    assert json.loads(resp.content) == CQ_200_ECHO
    for key in ("status", "patch_id", "completed_at"):
        assert key in resp.json(), key


def test_the_optional_evidence_body_reaches_cq_verbatim(quilt_client, cq):
    """REQUEST side, and the one CQ flagged hardest.

    A body that is usually absent is exactly what a schema forgets to
    model, and that is the `to_name` shape: SS sent it, an unmodelled
    field on our hop dropped it, SS saw a correct send and CQ saw a
    complete request that simply lacked it, so neither endpoint held any
    evidence anything was wrong. It lived only on the middle hop.
    """
    cq.install(CQ_200_ECHO, status=200)
    body = {"evidence": "Shipped it in #722, see the merged PR"}

    quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/complete", json=body)

    method, path, sent = _outbound(cq)
    assert method == "POST"
    assert path.endswith(f"/patches/{PATCH}/complete")
    assert sent == body, (
        "the evidence body did not reach CQ verbatim, which is invisible "
        "from both endpoints and only findable here")


def test_an_absent_body_stays_absent_rather_than_becoming_a_shape(quilt_client, cq):
    """The other half: a POST with no body must not acquire one. An
    invented `{}` or `{"evidence": null}` is a different request from the
    one SS made, and CQ owns what that means."""
    cq.install(CQ_200_ECHO, status=200)

    quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/complete")

    _, _, sent = _outbound(cq)
    assert sent is None, f"we invented a body: {sent!r}"


@pytest.mark.parametrize("verb", VERBS)
def test_an_unknown_field_in_the_body_is_not_dropped(quilt_client, cq, verb):
    """GP holds no schema for these bodies on purpose. A field CQ adds
    after this test was written must pass through, or we are back to a
    gateway quietly eating a key."""
    cq.install(CQ_200_ECHO, status=200)
    body = {"evidence": "x", "invented_later": {"deep": [1, None, "z"]}}

    quilt_client.post(f"/v1/quilt/{USER}/patches/{PATCH}/{verb}", json=body)

    _, _, sent = _outbound(cq)
    assert sent == body
