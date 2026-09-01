"""GET /v1/projects/{user}/resolve — the CQ project-resolve passthrough.

Two separate properties, tested at two levels, because they fail
differently.

REACHABILITY. `/v1/projects/{user_id}/{project_id}` MATCHES `.../resolve`
with `project_id = "resolve"`. Right now only PATCH and POST are declared
on that pattern, so a GET falling through answers 405, which is loud. The
day somebody adds a project-detail GET below this route, declaration order
becomes the only thing between a resolve call and a plausible 200 for a
project named "resolve" — an answer to a question nobody asked, which is
the one failure a client cannot see. SS flagged the shape before writing
the URL; these tests are what keep it flagged.

PAYLOAD FIDELITY. CQ answers 200 for all three outcomes and distinguishes
them by WHICH FIELDS ARE POPULATED, so an explicit null and an absent key
carry different meanings and neither may be normalised into the other.
And CQ returns the exact stored name, which in real data contains a
DOUBLE SPACE. These run the real `_cq_proxy` with only CQ's HTTP response
stubbed, because a passthrough guarantee that rests on the absence of code
is not a guarantee.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-resolve-1"
# The real string from Scott's roster. Two spaces, and that is the point.
DOUBLE_SPACE_NAME = "Immigration  Interview App"


def _user(user_id: str = USER) -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_resolve", tier="free",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def resolve_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def other_user_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user("someone-else")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def proxy():
    """Capture what we would have sent upstream to CQ."""
    with patch("app.routers.cq_proxy._cq_proxy", new_callable=AsyncMock) as m:
        from fastapi.responses import JSONResponse
        m.return_value = JSONResponse(status_code=200, content={"ok": True})
        yield m


# --- reachability: the route is not swallowed --------------------------------

def _matched_route(path: str, method: str = "GET"):
    """Which route would Starlette actually serve for this URL?

    This is the question, and `path.endswith("/resolve")` on the forwarded
    string is NOT it: a project-detail handler shadowing this route would
    build the very same upstream string from `project_id="resolve"`, so an
    assertion on the string passes while the wrong handler runs. Matching
    the way the router matches is the only version that cannot be fooled.
    """
    from starlette.routing import Match
    scope = {"type": "http", "method": method, "path": path, "headers": [],
             "query_string": b"", "root_path": ""}
    for r in app.routes:
        if r.matches(scope)[0] == Match.FULL:
            return r
    return None


def test_resolve_is_not_swallowed_by_the_project_id_pattern():
    """THE ordering test, named in the comment above the route."""
    r = _matched_route(f"/v1/projects/{USER}/resolve")
    assert r is not None, "no route matches GET .../resolve at all"
    assert r.endpoint.__name__ == "resolve_project", (
        f"GET .../resolve is being served by {r.endpoint.__name__} "
        f"(path template {r.path}), not the resolve handler. A route on the "
        f"{{project_id}} pattern is declared ABOVE it and is swallowing "
        f"'resolve' as a project id. Move resolve up.")
    assert r.path == "/v1/projects/{user_id}/resolve"


def test_resolve_is_declared_before_any_project_id_get():
    """The invariant the route comment claims, asserted structurally.

    Adding a project-detail GET is a reasonable thing to do. Adding it
    ABOVE this route is the mistake, and it is invisible: both answer 200
    and the wrong one answers a question nobody asked.
    """
    order = [r for r in app.routes
             if getattr(r, "path", "").startswith("/v1/projects/")
             and "GET" in (getattr(r, "methods", None) or set())]
    paths = [r.path for r in order]
    assert "/v1/projects/{user_id}/resolve" in paths, paths
    i_resolve = paths.index("/v1/projects/{user_id}/resolve")
    for i, pth in enumerate(paths):
        if pth == "/v1/projects/{user_id}/{project_id}":
            assert i_resolve < i, (
                "a project-detail GET is declared ABOVE resolve, so every "
                "resolve call now returns a lookup for a project named "
                "'resolve'. Swap the declaration order.")


def test_resolve_reaches_the_forward_with_the_expected_upstream_path(
        resolve_client, proxy):
    r = resolve_client.get(f"/v1/projects/{USER}/resolve?project_id=p-1")
    assert r.status_code == 200, r.text
    assert proxy.await_count == 1, "the resolve handler was never reached"
    assert proxy.await_args.args[0] == "GET"
    assert proxy.await_args.args[1].endswith("/resolve")


def test_resolve_refuses_another_users_projects(other_user_client, proxy):
    r = other_user_client.get(f"/v1/projects/{USER}/resolve?project_id=p-1")
    assert r.status_code == 403
    assert proxy.await_count == 0, "forwarded before checking ownership"


# --- the query echo ----------------------------------------------------------

@pytest.mark.parametrize("q", [
    "project_id=p-1",
    "name=Foo",
    "name=Immigration%20%20Interview%20App",   # the double space, encoded
    "name=Foo&project_id=p-1",
])
def test_the_query_string_is_forwarded_verbatim(resolve_client, proxy, q):
    """CQ echoes the query back precisely so a dropped param is visible to
    the caller. That check only works if we forward the raw string instead
    of rebuilding it from parsed params."""
    r = resolve_client.get(f"/v1/projects/{USER}/resolve?{q}")
    assert r.status_code == 200, r.text
    assert proxy.await_args.kwargs.get("query") == q


# --- payload fidelity: the real _cq_proxy, only CQ's HTTP stubbed ------------

def _cq_answers(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = ""
    inst = AsyncMock()
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    inst.request = AsyncMock(return_value=resp)
    return inst


@pytest.fixture
def cq_returns(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings",
                        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))

    def _install(payload, status=200):
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient",
                            lambda *a, **k: _cq_answers(payload, status))
    return _install


RESOLVED = {"project_id": "p-1", "name": DOUBLE_SPACE_NAME, "candidates": [],
            "echo": {"project_id": "p-1"}}
AMBIGUOUS = {"project_id": None,
             "candidates": [{"project_id": "p-1", "name": DOUBLE_SPACE_NAME},
                            {"project_id": "p-2", "name": "Immigration App"}],
             "echo": {"name": DOUBLE_SPACE_NAME}}
UNKNOWN = {"candidates": [], "echo": {"name": "Nope"}}


def test_ambiguous_keeps_project_id_as_an_EXPLICIT_null(resolve_client, cq_returns):
    """`ambiguous` is 'project_id present and null'. `unknown` is
    'project_id absent'. Coercing either into the other tells the client a
    different story with the same status code."""
    cq_returns(AMBIGUOUS)
    r = resolve_client.get(f"/v1/projects/{USER}/resolve?name=x")
    assert r.status_code == 200, r.text
    raw = json.loads(r.content)
    assert "project_id" in raw, "the explicit null was dropped to an absent key"
    assert raw["project_id"] is None
    assert len(raw["candidates"]) == 2


def test_unknown_keeps_project_id_ABSENT(resolve_client, cq_returns):
    cq_returns(UNKNOWN)
    r = resolve_client.get(f"/v1/projects/{USER}/resolve?name=x")
    assert r.status_code == 200, r.text
    raw = json.loads(r.content)
    assert "project_id" not in raw, (
        "an absent key was materialised as null, which turns `unknown` into "
        "`ambiguous` on the wire")


@pytest.mark.parametrize("payload", [RESOLVED, AMBIGUOUS])
def test_the_exact_stored_name_survives_including_the_double_space(
        resolve_client, cq_returns, payload):
    """The client corrects its own name string against CQ's stored one, so
    collapsing whitespace here destroys the only signal the feature runs
    on, silently and with a 200."""
    cq_returns(payload)
    r = resolve_client.get(f"/v1/projects/{USER}/resolve?name=x")
    assert r.status_code == 200, r.text
    body = r.content.decode()
    assert DOUBLE_SPACE_NAME in json.dumps(json.loads(body)), body[:300]
    assert "  " in body, "the double space was collapsed somewhere in the hop"


def test_the_echo_block_reaches_the_client_unmodified(resolve_client, cq_returns):
    cq_returns(RESOLVED)
    r = resolve_client.get(f"/v1/projects/{USER}/resolve?project_id=p-1")
    assert json.loads(r.content)["echo"] == {"project_id": "p-1"}
