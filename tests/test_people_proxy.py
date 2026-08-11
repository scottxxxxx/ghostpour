"""People proxy routes (2026-08-03).

A Shoulder Surf Person is a projection of a CQ person entity, with CQ as
the source of truth. GP is the gateway, and until these routes existed
SS's first People call 404'd here, which on their side reads as a client
bug. CQ flagged it before SS started building.

The assertion that matters most is query passthrough. `since` does not
error when dropped, it silently turns a delta sync into a full one, which
is the same class of bug that made every quilt poll return the whole
quilt.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord

USER = "user-people-1"


def _user(user_id: str = USER, tier: str = "free") -> UserRecord:
    """Free by default: People is enabled on every tier, so the free user
    is the one that proves the gate is open rather than incidentally
    passing because the test user was Pro."""
    return UserRecord(
        id=user_id, apple_sub="sub_people", tier=tier,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


# Uses the conftest `client`, which runs the app lifespan, so the
# entitlement check reads the REAL matrix (people: enabled for every tier)
# instead of a stub. A bare TestClient skips lifespan and leaves
# app.state.remote_configs unset.
@pytest.fixture
def people_client(client):
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


ROUTES = [
    ("get", f"/v1/people/{USER}", None),
    ("get", f"/v1/people/{USER}/network", None),
    ("get", f"/v1/people/{USER}/ent-9", None),
    ("post", f"/v1/people/{USER}", {"name": "Ada"}),
    ("post", f"/v1/people/{USER}/merge", {"a": "1", "b": "2"}),
    ("post", f"/v1/people/{USER}/keep-separate", {"a": "1", "b": "2"}),
    ("post", f"/v1/people/{USER}/ent-9/confirm", {}),
    ("post", f"/v1/people/{USER}/ent-9/rename", {"name": "Ada Lovelace"}),
    ("post", f"/v1/people/{USER}/ent-9/not-a-person", {}),
    ("delete", f"/v1/people/{USER}/ent-9/not-a-person", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_route_exists_and_reaches_cq(people_client, proxy, method, path, body):
    """The whole point: none of these may 404 at the gateway."""
    resp = getattr(people_client, method)(path, **({"json": body} if body is not None else {}))
    assert resp.status_code == 200, f"{method.upper()} {path} -> {resp.status_code}"
    assert proxy.await_count == 1


def test_list_forwards_every_query_param_verbatim(people_client, proxy):
    """since / confirmed / min_meetings / limit all have to survive the
    proxy. `since` especially: dropping it degrades quietly."""
    q = "since=2026-08-01T00%3A00%3A00Z&confirmed=true&min_meetings=5&limit=50"
    resp = people_client.get(f"/v1/people/{USER}?{q}")
    assert resp.status_code == 200
    forwarded = proxy.await_args.kwargs["query"]
    for param in ("since=", "confirmed=true", "min_meetings=5", "limit=50"):
        assert param in forwarded, f"{param} was dropped on the way to CQ"


def test_the_query_string_survives_byte_for_byte(people_client, proxy):
    """SS asked for the raw-byte version of the check above, and they were
    right to: presence of `since=` is not the same claim as the VALUE
    arriving intact. A timestamp mangled in transit is worse than a dropped
    one, because CQ answers a different question confidently.

    Percent-encoding is the specific risk. `2026-08-01T00:00:00Z` arrives as
    `...T00%3A00%3A00Z`, and anything that parses and re-serialises the
    query can normalise, double-encode, or reorder it. We forward the raw
    string, so this asserts identity, not membership."""
    q = "since=2026-08-01T00%3A00%3A00Z&confirmed=true&min_meetings=5&limit=50"
    people_client.get(f"/v1/people/{USER}?{q}")
    assert proxy.await_args.kwargs["query"] == q


def test_a_since_only_delta_sync_is_not_silently_widened(people_client, proxy):
    """The failure SS named, in isolation. Dropping `since` does not error:
    it turns a delta sync into a full one, the client gets a correct-looking
    answer, and nobody sees it fail. Same class as the quilt poll that
    returned the whole quilt on every launch."""
    people_client.get(f"/v1/people/{USER}?since=2026-08-01T00%3A00%3A00Z")
    assert proxy.await_args.kwargs["query"] == "since=2026-08-01T00%3A00%3A00Z"


@pytest.mark.parametrize("q", [
    "since=2026-08-01T00%3A00%3A00Z",
    "since=2026-08-01T00:00:00Z",          # unencoded colons, also legal
    "limit=50&since=2026-08-01T00%3A00%3A00Z",   # order preserved
    "since=2026-08-01T00%3A00%3A00%2B01%3A00",   # +01:00 offset, encoded
])
def test_since_survives_in_every_shape_a_client_might_send(people_client, proxy, q):
    """We do not parse it, so we do not get to be opinionated about which
    encoding is correct. Whatever the client sent is what CQ sees."""
    people_client.get(f"/v1/people/{USER}?{q}")
    assert proxy.await_args.kwargs["query"] == q


def test_no_query_string_forwards_none_not_empty(people_client, proxy):
    """An empty string would send CQ a bare '?', which is not the same
    request."""
    people_client.get(f"/v1/people/{USER}")
    assert proxy.await_args.kwargs["query"] is None


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_cannot_reach_another_users_people(other_user_client, proxy, method, path, body):
    resp = getattr(other_user_client, method)(
        path, **({"json": body} if body is not None else {}))
    assert resp.status_code == 403
    assert proxy.await_count == 0


def test_merge_is_not_swallowed_by_the_entity_route(people_client, proxy):
    """`merge` and `keep-separate` are literal segments sitting where an
    entity_id would go. If ordering ever regresses, they get proxied to
    /people/{user}/merge as an entity fetch instead."""
    people_client.post(f"/v1/people/{USER}/merge", json={"a": "1"})
    assert proxy.await_args.args[1].endswith("/merge")
    assert proxy.await_args.args[0] == "POST"


# --- not-a-person (2026-08-10, People/Memory boundary) ---------------
#
# Suppress an ASR-garbage entity, and lift the suppression. Live on CQ's
# side; without these two rows in our table every device call 404s here
# while CQ's own socket answers 200, which is exactly how `uncomplete`
# failed. The route table is the contract; see
# test_the_whole_people_verb_surface_is_carried below.


def test_the_whole_people_verb_surface_is_carried():
    """Same assertion the patch verbs got after uncomplete 404'd from
    devices: asserted against the app's route table rather than by calling
    each one, because a missing route and a route that errors are
    different failures and only the table can tell them apart."""
    from app.main import app
    have = {(m, getattr(r, "path", ""))
            for r in app.routes
            for m in (getattr(r, "methods", None) or [])}
    surface = (
        ("GET", "/v1/people/{user_id}"),
        ("POST", "/v1/people/{user_id}"),
        ("GET", "/v1/people/{user_id}/network"),
        ("GET", "/v1/people/{user_id}/{entity_id}"),
        ("POST", "/v1/people/{user_id}/merge"),
        ("POST", "/v1/people/{user_id}/keep-separate"),
        ("POST", "/v1/people/{user_id}/{entity_id}/confirm"),
        ("POST", "/v1/people/{user_id}/{entity_id}/rename"),
        ("POST", "/v1/people/{user_id}/{entity_id}/not-a-person"),
        ("DELETE", "/v1/people/{user_id}/{entity_id}/not-a-person"),
    )
    for method, path in surface:
        assert (method, path) in have, f"{method} {path} is not in the route table"


def test_lifting_a_suppression_is_a_delete_on_the_same_path(people_client, proxy):
    """The undo is a DELETE on the same path, not a flag on the POST, same
    shape as shelve/unshelve. If both landed on the same CQ call, a lift
    would be indistinguishable from a repeat suppression."""
    people_client.post(f"/v1/people/{USER}/ent-9/not-a-person", json={})
    first = proxy.await_args
    people_client.request("DELETE", f"/v1/people/{USER}/ent-9/not-a-person")
    second = proxy.await_args
    assert first.args[0] == "POST"
    assert second.args[0] == "DELETE"
    assert first.args[1] == second.args[1]
    assert first.args[1].endswith("/ent-9/not-a-person")


def test_not_a_person_body_is_forwarded_verbatim(people_client, proxy):
    """CQ owns the shape. The body is optional and untyped, so a field
    they add later reaches them without us shipping anything."""
    sent = {"surface_form": "Horm Hel", "nested": {"x": 1}}
    people_client.post(f"/v1/people/{USER}/ent-9/not-a-person", json=sent)
    assert proxy.await_args.kwargs["body"] == sent


def test_rename_body_is_forwarded_verbatim(people_client, proxy):
    """Rename's body carries the payload that matters: {"name", "source"}.
    CQ owns the shape and validates it (placeholder names, self names,
    NAME_TAKEN), so nothing here may model or trim it on the way through."""
    sent = {"name": "Ada Lovelace", "source": "user_typed", "later": {"x": 1}}
    people_client.post(f"/v1/people/{USER}/ent-9/rename", json=sent)
    assert proxy.await_args.kwargs["body"] == sent
    assert proxy.await_args.args[1].endswith("/ent-9/rename")


def test_not_a_person_with_no_body_forwards_none(people_client, proxy):
    """Bodies are optional on both verbs. An absent body must forward as
    None, not as an empty object CQ has to guess about."""
    people_client.request("DELETE", f"/v1/people/{USER}/ent-9/not-a-person")
    assert proxy.await_args.kwargs["body"] is None


def test_disabled_entitlement_actually_closes_the_door(people_client, proxy, monkeypatch):
    """People is enabled everywhere today, so the toggle would be
    decorative unless flipping it is enforced here."""
    with patch("app.services.entitlements.entitlement_state",
               lambda *a, **k: "disabled"):
        resp = people_client.get(f"/v1/people/{USER}")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "feature_disabled"
    assert proxy.await_count == 0


# --- tier simulation (2026-08-04) ------------------------------------
#
# SS found that _require_people gated on user.tier while every other
# entitlement check in cq_proxy reads user.effective_tier, which is
# simulated_tier or tier. So People was the one feature that ignored admin
# tier simulation.
#
# Low severity today, because People is enabled on every tier and the
# guard fails open when the config store is unavailable, so it probably
# never returned a wrong answer for a real user. The cost is narrower:
# simulation is the tool you reach for to TEST this gate, and it was the
# one input the gate did not read. It sharpens the moment People becomes
# tier-conditional, and an upgrade_cta is already served for it.


def _simulated(user_id: str, real: str, simulated: str) -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_sim", tier=real, simulated_tier=simulated,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


def test_the_gate_reads_the_simulated_tier(client, proxy, monkeypatch):
    """Simulating a tier whose People state is disabled must close the door,
    even though the account's real tier is enabled."""
    app.dependency_overrides[get_current_user] = lambda: _simulated(
        USER, real="pro", simulated="free")
    try:
        def _state(configs, tier, feature):
            return "disabled" if tier == "free" else "enabled"

        with patch("app.services.entitlements.entitlement_state", _state):
            resp = client.get(f"/v1/people/{USER}")
        assert resp.status_code == 403, (
            "the gate read the real tier and ignored the simulation")
        assert resp.json()["detail"]["code"] == "feature_disabled"
        assert proxy.await_count == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_simulation_can_also_open_the_door(client, proxy, monkeypatch):
    """The reverse direction, so the fix is not just 'deny more often'."""
    app.dependency_overrides[get_current_user] = lambda: _simulated(
        USER, real="free", simulated="pro")
    try:
        def _state(configs, tier, feature):
            return "disabled" if tier == "free" else "enabled"

        with patch("app.services.entitlements.entitlement_state", _state):
            resp = client.get(f"/v1/people/{USER}")
        assert resp.status_code == 200
        assert proxy.await_count == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
