"""GET /v1/quilt/{user_id}/insights (design 10c Memory tab).

Carried at the edge BEFORE CQ's side merges (CQ PR #227), which is the
correct order: routes are additive only at the gateway, so a path we do
not carry 404s for every device no matter what the origin does, while a
carried path that 404s upstream is just CQ's answer passing through
until their deploy lands.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord

USER = "user-insights-1"


def _user(user_id: str = USER) -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_insights", tier="free",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def proxy():
    """Capture what we would have sent upstream to CQ."""
    with patch("app.routers.cq_proxy._cq_proxy", new_callable=AsyncMock) as m:
        from fastapi.responses import JSONResponse
        m.return_value = JSONResponse(
            status_code=200, content={"user_id": USER, "follow_up": None})
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


def test_the_route_is_in_the_table():
    """The claim that matters is the table's, not a call's: a missing
    route and a route that errors are different failures and only the
    table can tell them apart."""
    have = {(m, getattr(r, "path", ""))
            for r in app.routes
            for m in (getattr(r, "methods", None) or [])}
    assert ("GET", "/v1/quilt/{user_id}/insights") in have


def test_insights_reaches_cq_on_the_insights_path(owner_client, proxy):
    resp = owner_client.get(f"/v1/quilt/{USER}/insights")
    assert resp.status_code == 200
    assert proxy.await_count == 1
    assert proxy.await_args.args[0] == "GET"
    assert proxy.await_args.args[1].endswith(f"/{USER}/insights")


def test_insights_is_not_swallowed_by_the_bare_quilt_route(owner_client, proxy):
    """GET /quilt/{user_id} is declared first; a segment-crossing match
    would proxy this to CQ's bare quilt path and answer with the whole
    quilt instead of insights."""
    owner_client.get(f"/v1/quilt/{USER}/insights")
    assert proxy.await_args.args[1] != f"/v1/quilt/{USER}"


def test_query_string_forwards_verbatim(owner_client, proxy):
    """No params exist today; CQ may add some later and they must not
    need a GP deploy to arrive."""
    owner_client.get(f"/v1/quilt/{USER}/insights?horizon=7d")
    assert proxy.await_args.kwargs["query"] == "horizon=7d"


def test_no_query_forwards_none_not_empty(owner_client, proxy):
    owner_client.get(f"/v1/quilt/{USER}/insights")
    assert proxy.await_args.kwargs["query"] is None


def test_cannot_read_another_users_insights(other_client, proxy):
    resp = other_client.get(f"/v1/quilt/{USER}/insights")
    assert resp.status_code == 403
    assert proxy.await_count == 0, "the guard must run before we call CQ"
