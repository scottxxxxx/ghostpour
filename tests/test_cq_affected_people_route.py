"""GP carries CQ's affected-people route (CQ #420), and does not reshape it.

The 2026-08-10 lesson: GP's edge declares EXACT routes with no prefix and no
wildcard, so a CQ route GP has not carried 404s from every device while CQ's
own socket answers 200. Neither endpoint can see it. This file pins that the
route exists, that it is owner-guarded, and that the body crosses UNTOUCHED.

CQ named the two places to sabotage: the nested `signals` object and the
`confidence` string, whose vocabulary is OPEN. A response model here would
drop a signal they add later and look correct from both ends.

⚠ WHAT EACH TEST CAN AND CANNOT CATCH, measured by sabotage rather than
assumed. Deleting the route fails all five. Adding `response_model=dict`
fails ONLY `test_the_handler_has_no_response_model`, and NOT the passthrough
test, because the passthrough test calls the handler coroutine directly and
FastAPI's serialization layer sits ABOVE that call. So the passthrough test
proves the HANDLER does not reshape the body, and the structural test is the
only thing standing between us and the framework reshaping it. Neither is
sufficient alone, and the passthrough test alone would have read as coverage
it does not have.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from fastapi import HTTPException

from app.routers import cq_proxy


class _Stub:
    pass


def _stub_request(query: str = "limit=50"):
    r = _Stub()
    r.state = _Stub()
    r.state.app_id = "shouldersurf"
    r.url = _Stub()
    r.url.query = query
    r.headers = {}
    return r


def _stub_user(uid: str):
    u = _Stub()
    u.id = uid
    u.effective_tier = "pro"
    return u


def test_the_route_is_declared_at_the_exact_path():
    """A route GP has not carried 404s from every phone. No wildcard saves us."""
    paths = {r.path for r in cq_proxy.router.routes}
    assert "/projects/{user_id}/{project_id}/affected-people" in paths, sorted(
        p for p in paths if "projects" in p)


def test_it_is_a_GET_and_carries_no_body():
    route = next(r for r in cq_proxy.router.routes
                 if r.path == "/projects/{user_id}/{project_id}/affected-people")
    assert route.methods == {"GET"}, route.methods


def test_the_response_body_crosses_untouched(monkeypatch):
    """The whole point. Nested `signals` and an UNKNOWN `confidence` value
    both survive, because nothing types this body.

    This calls the REAL HANDLER. An earlier version of this test called the
    stub directly and asserted the stub returned what the stub was given,
    which is a test that cannot fail on the bug it was written for.

    `confidence` is deliberately a value neither team has shipped: an open
    vocabulary means today's set is not the contract."""
    upstream = {
        "people": [
            {"entity_id": "e1", "name": "Ada",
             "signals": {"patches_in_project": 12, "patches_outside": 0,
                         "meetings_outside": 0},
             "confidence": "a_value_neither_team_has_shipped"},
        ],
        "total_affected": 305,
        "counts_by_confidence": {"high": 96, "low": 51},
        "duplicate_names": ["Scott"],
        "definition": "appears in this project and nowhere else",
        "an_unmodelled_key_cq_adds_later": {"nested": ["deep", 1, None]},
    }
    seen = {}

    async def fake_proxy(method, path, *a, **kw):
        seen["method"], seen["path"] = method, path
        seen["query"] = kw.get("query")
        seen["request_kw"] = "request" in kw
        return upstream

    monkeypatch.setattr(cq_proxy, "_cq_proxy", fake_proxy)

    req = _stub_request()
    user = _stub_user("scott")
    got = asyncio.run(cq_proxy.project_affected_people(
        request=req, user_id="scott", project_id="proj1", user=user))

    assert got is upstream, "the handler reshaped the body on the way through"
    assert got["people"][0]["signals"]["patches_outside"] == 0
    assert got["people"][0]["confidence"] == "a_value_neither_team_has_shipped"
    assert got["an_unmodelled_key_cq_adds_later"]["nested"] == ["deep", 1, None]
    assert got["total_affected"] == 305, (
        "total_affected is computed BEFORE CQ's cap and must not be derived "
        "from len(people)")
    assert got["total_affected"] != len(got["people"])

    assert seen["method"] == "GET"
    assert seen["path"].endswith("/proj1/affected-people"), seen["path"]
    assert seen["query"] == "limit=50", "the query string must ride verbatim"
    assert seen["request_kw"], "request must be passed so headers forward"


def test_a_caller_cannot_read_another_users_project(monkeypatch):
    """Ownership guard. Sibling /projects routes guard on ownership alone and
    so does this one; _require_people would add an entitlement gate no other
    project route has and block a delete confirmation the user is entitled to."""
    async def explode(*a, **kw):
        raise AssertionError("CQ was called despite the ownership guard")
    monkeypatch.setattr(cq_proxy, "_cq_proxy", explode)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(cq_proxy.project_affected_people(
            request=_stub_request(), user_id="someone_else",
            project_id="p", user=_stub_user("scott")))
    assert exc.value.status_code == 403


def test_the_handler_has_no_response_model():
    """A response_model would validate and therefore DROP unmodelled keys.
    Asserting the absence is the only way to pin 'we chose not to type it'."""
    route = next(r for r in cq_proxy.router.routes
                 if r.path == "/projects/{user_id}/{project_id}/affected-people")
    assert getattr(route, "response_model", None) is None
