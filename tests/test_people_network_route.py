"""GET /v1/people/{user_id}/network (design 13b orbit graph).

Carried at the edge BEFORE CQ's side merges (their PR is open, not yet
deployed), which is the correct order: routes are additive only at the
gateway, so a path we do not carry 404s for every device no matter what
the origin does, while a carried path that 404s upstream is just CQ's
answer passing through until their deploy lands.

Gating is the free People lane, exactly as the sibling people reads:
_require_people (ownership, then the people entitlement) runs before any
upstream call. No new entitlement mapping, and no verdict-matrix row;
that matrix governs the capture write path and this is a read.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord

USER = "user-network-1"


def _user(user_id: str = USER) -> UserRecord:
    """Free by default, same reasoning as the people siblings: the free
    user is the one that proves the lane is open rather than incidentally
    passing because the test user was Pro."""
    return UserRecord(
        id=user_id, apple_sub="sub_network", tier="free",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def proxy():
    """Capture what we would have sent upstream to CQ."""
    with patch("app.routers.cq_proxy._cq_proxy", new_callable=AsyncMock) as m:
        from fastapi.responses import JSONResponse
        m.return_value = JSONResponse(
            status_code=200,
            content={
                "version": 1, "computed_at": None, "caps": {},
                "nodes": [], "edges": [], "clusters": [], "positions": {},
            })
        yield m


# Uses the conftest `client`, which runs the app lifespan, so the
# entitlement check reads the REAL matrix (people: enabled for every
# tier) instead of a stub.
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


def test_the_route_is_in_the_table():
    """The claim that matters is the table's, not a call's: a missing
    route and a route that errors are different failures and only the
    table can tell them apart."""
    have = {(m, getattr(r, "path", ""))
            for r in app.routes
            for m in (getattr(r, "methods", None) or [])}
    assert ("GET", "/v1/people/{user_id}/network") in have


def test_network_is_declared_before_the_entity_detail_route():
    """`network` sits where an entity_id would go. Today a swallowed
    match would build the same upstream path by coincidence; this pins
    the ordering so that stays an intent when either route changes."""
    paths = [getattr(r, "path", "") for r in app.routes
             if "GET" in (getattr(r, "methods", None) or [])]
    network_idx = paths.index("/v1/people/{user_id}/network")
    detail_idx = paths.index("/v1/people/{user_id}/{entity_id}")
    assert network_idx < detail_idx, (
        "the network route must be declared before {entity_id} or the "
        "literal segment is swallowed as an entity fetch")


def test_network_reaches_cq_on_the_network_path(owner_client, proxy):
    resp = owner_client.get(f"/v1/people/{USER}/network")
    assert resp.status_code == 200
    assert proxy.await_count == 1
    assert proxy.await_args.args[0] == "GET"
    assert proxy.await_args.args[1].endswith(f"/{USER}/network")


def test_no_query_parameters_are_forwarded(owner_client, proxy):
    """This route takes no query parameters and forwards none, unlike
    the list route: a stray param must not reach CQ and change the
    question being asked."""
    owner_client.get(f"/v1/people/{USER}/network?layout=orbit")
    assert proxy.await_args.kwargs.get("query") is None


def test_cannot_read_another_users_network(other_client, proxy):
    resp = other_client.get(f"/v1/people/{USER}/network")
    assert resp.status_code == 403
    assert proxy.await_count == 0, "the guard must run before we call CQ"


def test_disabled_people_entitlement_closes_the_door(owner_client, proxy):
    """The network graph rides the People lane, so flipping the people
    row to disabled has to close this route too, not just the roster."""
    with patch("app.services.entitlements.entitlement_state",
               lambda *a, **k: "disabled"):
        resp = owner_client.get(f"/v1/people/{USER}/network")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "feature_disabled"
    assert proxy.await_count == 0
