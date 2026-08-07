"""Ledger triage: vouch and shelve (2026-08-07, SS Turn 4 via CQ).

SS's ledger flow ends in Done, Still live, or Let it go. `complete` is Done
and already existed. `vouch` and `shelve` are the other two, and CQ flagged
them while they were still a design rather than after SS built against
them, because our edge has real routes with their own guard rather than
entries on a generic passthrough.

The distinction the shapes have to preserve: shelving is not completing.
A user dismissing something they were never going to do must not be
indistinguishable from a user finishing it, or the ledger stops meaning
anything. Hence a separate verb, and hence a DELETE to undo it.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord

USER = "user-ledger-1"

ROUTES = [
    ("post", f"/v1/quilt/{USER}/patches/p1/complete"),
    ("post", f"/v1/quilt/{USER}/patches/p1/vouch"),
    ("post", f"/v1/quilt/{USER}/patches/p1/shelve"),
    ("delete", f"/v1/quilt/{USER}/patches/p1/shelve"),
]


def _user(user_id: str = USER) -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_ledger", tier="free",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def proxy():
    """Capture what we would have sent upstream to CQ."""
    with patch("app.routers.cq_proxy._cq_proxy", new_callable=AsyncMock) as m:
        from fastapi.responses import JSONResponse
        m.return_value = JSONResponse(status_code=200, content={"ok": True})
        yield m


@pytest.fixture
def owner_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def other_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user("someone-else")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_triage_route_exists_and_reaches_cq(owner_client, proxy, method, path):
    resp = owner_client.request(method.upper(), path, json={})
    assert resp.status_code == 200, resp.text
    assert proxy.await_count == 1


@pytest.mark.parametrize("method,path", ROUTES)
def test_you_cannot_triage_another_users_ledger(other_client, proxy, method, path):
    resp = other_client.request(method.upper(), path, json={})
    assert resp.status_code == 403
    assert proxy.await_count == 0, "the guard must run before we call CQ"


def test_shelve_and_unshelve_are_different_calls(owner_client, proxy):
    """The undo is a DELETE on the same path, not a flag on the POST. If
    both landed on the same CQ call, undo would be indistinguishable from
    a repeat shelve."""
    owner_client.post(f"/v1/quilt/{USER}/patches/p1/shelve", json={})
    first = proxy.await_args
    owner_client.request("DELETE", f"/v1/quilt/{USER}/patches/p1/shelve", json={})
    second = proxy.await_args
    assert first.args[0] == "POST"
    assert second.args[0] == "DELETE"
    assert first.args[1] == second.args[1]


def test_shelve_is_not_complete(owner_client, proxy):
    """Dismissing something you were never going to do must not look like
    finishing it. Different paths, so CQ can tell them apart."""
    owner_client.post(f"/v1/quilt/{USER}/patches/p1/complete", json={})
    completed = proxy.await_args.args[1]
    owner_client.post(f"/v1/quilt/{USER}/patches/p1/shelve", json={})
    shelved = proxy.await_args.args[1]
    assert completed != shelved
    assert completed.endswith("/complete")
    assert shelved.endswith("/shelve")


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_body_is_forwarded_verbatim(owner_client, proxy, method, path):
    """CQ owns the contract. We do not model it, so a field they add later
    reaches them without us shipping anything."""
    sent = {"reason": "not-a-field-we-know", "nested": {"x": 1}}
    owner_client.request(method.upper(), path, json=sent)
    assert proxy.await_args.args[2] == sent
