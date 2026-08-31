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

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dependencies import get_current_user
from app.routers import cq_proxy
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


# --- Response side ---
#
# Everything above pins what we SEND. Nothing pinned what we RETURN, and
# the response is where GP has eaten keys before. `_cq_proxy` parses CQ's
# JSON and re-serializes it (there is no response model and no allowlist),
# so the passthrough guarantee is real but was resting on the absence of
# code rather than on a test. These run the REAL `_cq_proxy` with only
# CQ's HTTP response stubbed, which is the difference that matters.


def _cq_answers(payload, status=200):
    """An httpx.AsyncClient stub whose request() returns `payload`."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = ""
    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.request = AsyncMock(return_value=resp)
    return instance


@pytest.fixture
def cq_returns(monkeypatch):
    """Serve `payload` from CQ through the real proxy body."""
    monkeypatch.setattr(
        cq_proxy, "get_settings",
        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))

    def _install(payload, status=200):
        monkeypatch.setattr(
            cq_proxy.httpx, "AsyncClient",
            lambda *a, **k: _cq_answers(payload, status))
    return _install


# The 16a person detail shape: insights carrying evidence rows, plus the
# keys CQ shipped in their #239. Deliberately includes fields GP has never
# heard of, at every depth, because that is the property under test.
INSIGHT_PAYLOAD = {
    "entity_id": "ent-9",
    "display_name": "Ada Lovelace",
    "capabilities": {"insights": True, "some_future_capability": "on"},
    "insights": [
        {
            "id": "ins-1",
            "kind": "pattern",
            "text": "Ships on Fridays",
            "decay_state": "fresh",
            "confidence": 0.8125,
            "do": "Ask before Thursday",
            "unknown_scalar": "carried",
            "evidence": [
                {
                    "text": "said it in standup",
                    "patch_ids": ["p-1", "p-2"],
                    "ingested_on": "2026-08-12",
                    "weight": 0.5,
                    "retracted_at": None,
                    "nested": {"deep": {"deeper": ["a", 1, None, 2.5]}},
                },
                {
                    "text": "again on the 3rd",
                    "patch_ids": [],
                    "ingested_on": None,
                    "weight": 0.0,
                },
            ],
        },
        {
            "id": "ins-2",
            "kind": "obligation_pattern",
            "decay_state": "decaying",
            "confidence": None,
            "evidence": [],
        },
    ],
    "obligations": [{"id": "ob-1", "owed_to": "them", "state": "open"}],
    "counts": {"meetings": 12, "insights": 2},
}


def test_person_detail_response_passes_through_verbatim(people_client, cq_returns):
    """Byte-equivalent, not merely equal-ish: nested unknown keys, empty
    lists, nulls, floats and ints all reach the client untouched. This is
    the guarantee that lets CQ add fields without a three-way audit."""
    cq_returns(INSIGHT_PAYLOAD)

    resp = people_client.get(f"/v1/people/{USER}/ent-9")

    assert resp.status_code == 200
    assert json.loads(resp.content) == INSIGHT_PAYLOAD
    # Named explicitly so a future reshaping breaks with a readable diff
    # rather than a whole-payload mismatch.
    got = resp.json()
    assert got["capabilities"]["insights"] is True
    ev = got["insights"][0]["evidence"][0]
    assert ev["text"] == "said it in standup"
    assert ev["patch_ids"] == ["p-1", "p-2"]
    assert ev["ingested_on"] == "2026-08-12"
    assert ev["retracted_at"] is None
    assert ev["nested"]["deep"]["deeper"] == ["a", 1, None, 2.5]
    assert got["insights"][0]["decay_state"] == "fresh"
    assert got["insights"][1]["confidence"] is None
    assert got["insights"][1]["evidence"] == []


def test_upstream_status_codes_survive_the_response_path(people_client, cq_returns):
    """A 404 from CQ is CQ's answer, not a proxy failure."""
    cq_returns({"detail": "unknown person"}, status=404)
    resp = people_client.get(f"/v1/people/{USER}/ent-9")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "unknown person"}


def test_one_nan_does_not_cost_the_whole_person(people_client, cq_returns, caplog):
    """json.loads accepts bare NaN; starlette renders with allow_nan=False.
    Before sanitizing, one bad confidence float raised mid-render and the
    generic handler returned 502 for the entire page. The number degrades
    to null, everything else survives, and the log says CQ misbehaved."""
    payload = json.loads(json.dumps(INSIGHT_PAYLOAD))
    payload["insights"][0]["confidence"] = float("nan")
    payload["insights"][0]["evidence"][0]["weight"] = float("inf")
    payload["insights"][1]["evidence"] = [{"weight": float("-inf")}]

    cq_returns(payload)
    with caplog.at_level(logging.WARNING):
        resp = people_client.get(f"/v1/people/{USER}/ent-9")

    assert resp.status_code == 200
    got = resp.json()
    assert got["insights"][0]["confidence"] is None
    assert got["insights"][0]["evidence"][0]["weight"] is None
    assert got["insights"][1]["evidence"][0]["weight"] is None
    # The rest of the page is intact, which is the whole point.
    assert got["insights"][0]["text"] == "Ships on Fridays"
    assert got["insights"][0]["evidence"][0]["patch_ids"] == ["p-1", "p-2"]
    assert got["obligations"][0]["owed_to"] == "them"
    assert "cq_proxy_non_finite_float" in caplog.text


def test_sanitizer_leaves_ordinary_payloads_alone():
    """Zero replacements on clean data, and the value is returned as-is:
    the guard must not become a quiet reshaper of its own."""
    cleaned, hits = cq_proxy._null_non_finite(INSIGHT_PAYLOAD)
    assert hits == 0
    assert cleaned == INSIGHT_PAYLOAD
    assert cq_proxy._null_non_finite(0.0) == (0.0, 0)
    assert cq_proxy._null_non_finite(True) == (True, 0)
    assert cq_proxy._null_non_finite("NaN") == ("NaN", 0)


# --- CQ #301 (2026-08-21): title, stated_roles, described_as ----------------
#
# Three new keys on the person detail, nested shapes, all nullable. The
# handler forwards CQ's body verbatim through _cq_proxy (no response
# model), so nothing CAN be modelled away; these tests are the receipt
# rather than the fix, per rule 3: a response-side hole on this hop is
# invisible from CQ's socket and from SS's decoder.

CQ301_PAYLOAD = {
    "entity_id": "ent-suresh",
    "display_name": "Suresh",
    "title": "scrum master on ABM project",
    "stated_roles": {
        "title": "scrum master on ABM project",
        "title_source": {"patch_id": "p-77", "origin_id": "MTG-1", "stated_at": "2026-08-19T14:00:00Z"},
        "items": [
            {"patch_id": "p-77", "text": "I am the scrum master on the ABM project", "project": "ABM",
             "project_id": "proj-abm", "origin_id": "MTG-1", "stated_at": "2026-08-19T14:00:00Z"},
            {"patch_id": "p-31", "text": "I run the standups", "project": None,
             "project_id": None, "origin_id": "MTG-0", "stated_at": "2026-08-02T10:00:00Z"},
        ],
    },
    "described_as": {
        "current": "runs ABM delivery",
        "changed_from": "coordinates QA handoffs",
        "iterations": 4,
        "history": [
            {"text": "runs ABM delivery", "first_observed_at": "2026-08-19T14:00:00Z",
             "last_observed_at": "2026-08-21T09:00:00Z", "observation_count": 3, "origin_id": "MTG-1"},
            {"text": "coordinates QA handoffs", "first_observed_at": "2026-08-02T10:00:00Z",
             "last_observed_at": "2026-08-12T10:00:00Z", "observation_count": 2, "origin_id": "MTG-0"},
        ],
        "truncated": False,
    },
    "insights": [],
}


@pytest.mark.parametrize("key", ["title", "stated_roles", "described_as"])
def test_cq301_key_arrives_byte_for_byte(people_client, cq_returns, key):
    cq_returns(CQ301_PAYLOAD)
    resp = people_client.get(f"/v1/people/{USER}/ent-suresh")
    assert resp.status_code == 200
    got = resp.json()
    assert key in got, f"{key} was modelled away on the hop"
    assert got[key] == CQ301_PAYLOAD[key]


@pytest.mark.parametrize("key", ["title", "stated_roles", "described_as"])
def test_cq301_null_survives_as_null_not_absent(people_client, cq_returns, key):
    """null means 'the app does not track this' or 'nothing stated'; absent
    would read as 'GP dropped it'. The decoder must be able to tell."""
    payload = json.loads(json.dumps(CQ301_PAYLOAD)); payload[key] = None
    cq_returns(payload)
    got = people_client.get(f"/v1/people/{USER}/ent-suresh").json()
    assert key in got and got[key] is None


# --- CQ `trajectory` (2026-08-22) ------------------------------------------
#
# The "how they're changing" hero card on Scott's new People card. One new
# nullable top-level key on the person detail; CQ locked the shape on
# 2026-08-22 and serves it from GET /v1/people/{user_id}/{entity_id}, the
# same path GP already proxies. (`working_with` is spec'd on CQ's side but
# not built, so there is deliberately no fixture for it here: a test
# against a promised shape proves nothing about the shape that ships.)
#
# Four properties, all of which fail SILENTLY rather than loudly if a
# middlebox eats them, which is why they are pinned:
#   1. `series` is ordered oldest meeting first. `sequence` is monotonic but
#      numerator and denominator are NOT and never will be, so a re-sort by
#      any value field rewrites the chart into a different claim that
#      nothing downstream can detect. CQ carries `sequence` INSIDE each
#      element as well as implied by position precisely so a re-sort is
#      detectable at the client; these tests are what say that redundancy
#      actually survives the hop.
#   2. Objects nested in an array nested in the object (`series`), and
#      arrays of strings nested in objects nested in the object
#      (`earlier.origin_ids`, `recent.origin_ids`).
#   3. THERE IS NOT ONE FLOAT IN THIS PAYLOAD, by CQ's design: no rate is
#      ever served pre-divided, so every count, span and series point is an
#      integer. GP's `_null_non_finite` only ever visits the float branch,
#      which makes that rule load-bearing on our side too. A float appearing
#      here is a CQ bug, and CQ asked for it to be caught by a test rather
#      than quietly walked by the NaN guard.
#   4. Empty is not null and null is not absent, in three places:
#      `trajectory` itself (null for most people BY DESIGN, gates are
#      strict, so null is the common case and not an edge), `supersedes`
#      (empty list when nothing is superseded), and `series`.
TRAJECTORY = {
    "lens": "how_theyre_changing",
    "display_order": 5,
    "measure_key": "closed_late",
    # PROPORTION: the numerator is a SUBSET of the denominator, 4 of the 7
    # dated commitments he closed. See the rate fixture below for the other
    # kind and for why the distinction has to survive the hop.
    "pair_kind": "proportion",
    "counted_noun": "items",
    "subject": "Suresh",
    "unit": "commitments closed after the date he gave",
    "valence": "unflattering_up",
    "movement": "up",
    "direction": "worse",
    "span_meetings": 9,
    "earlier": {"numerator": 1, "denominator": 6, "meetings": 4,
                "origin_ids": ["MTG-9", "MTG-8", "MTG-7"]},
    "recent": {"numerator": 4, "denominator": 7, "meetings": 5,
               "origin_ids": ["MTG-3", "MTG-2", "MTG-1"]},
    # Oldest first. Deliberately NOT sorted by numerator (0,1,0,2,2) and NOT
    # sorted by denominator (2,4,1,3,4), so a middlebox sorting on either
    # value field cannot pass by coinciding with the true order.
    "series": [
        {"origin_id": "MTG-9", "sequence": 1, "numerator": 0, "denominator": 2},
        {"origin_id": "MTG-8", "sequence": 2, "numerator": 1, "denominator": 4},
        {"origin_id": "MTG-5", "sequence": 3, "numerator": 0, "denominator": 1},
        {"origin_id": "MTG-3", "sequence": 4, "numerator": 2, "denominator": 3},
        {"origin_id": "MTG-1", "sequence": 5, "numerator": 2, "denominator": 4},
    ],
    "supersedes": [],
    "about_person": "ent-suresh",
    "text": "He is closing later than he used to.",
    "narrative": "Four of his last seven commitments closed after the date he gave you.",
    "do": "Ask for the date he actually believes, not the one that sounds good.",
}

TRAJECTORY_PAYLOAD = {
    "entity_id": "ent-suresh",
    "display_name": "Suresh",
    "trajectory": TRAJECTORY,
    "insights": [],
}


def _floats_in(value, path="trajectory"):
    """Every path in `value` holding a float. Bools are not floats and ints
    are not floats; this is looking for the real thing."""
    if isinstance(value, float):
        return [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _floats_in(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _floats_in(v, f"{path}[{i}]")]
    return []


def _cq_recording(payload, status=200):
    """Like `_cq_answers`, but keeps the AsyncMock so a test can read back
    the URL GP actually asked CQ for. The request side of this hop is a path
    and a query string and nothing else: a GET carries no body, so pinning
    the query string is the only honest request-side claim this endpoint
    supports, and a "request-side test for trajectory" would be decoration.
    CQ agreed and withdrew that ask on 2026-08-22."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = ""
    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.request = AsyncMock(return_value=resp)
    return instance


def test_trajectory_arrives_value_for_value(people_client, cq_returns):
    cq_returns(TRAJECTORY_PAYLOAD)
    resp = people_client.get(f"/v1/people/{USER}/ent-suresh")
    assert resp.status_code == 200
    got = resp.json()
    assert "trajectory" in got, "trajectory was modelled away on the hop"
    assert got["trajectory"] == TRAJECTORY


def test_trajectory_null_survives_as_null_not_absent(people_client, cq_returns):
    """null is the COMMON case here, not the edge: the card fires for a small
    minority of people by design. Absent would read as "GP dropped it" and
    the client cannot tell that from "CQ has nothing to say about them"."""
    payload = json.loads(json.dumps(TRAJECTORY_PAYLOAD)); payload["trajectory"] = None
    cq_returns(payload)
    got = people_client.get(f"/v1/people/{USER}/ent-suresh").json()
    assert "trajectory" in got and got["trajectory"] is None


def test_series_order_survives_and_sequence_still_matches_position(people_client, cq_returns):
    """The redundancy CQ built in only pays off if BOTH halves cross the hop:
    array position and the `sequence` carried inside each element. If a
    middlebox re-sorts, position and sequence disagree, and that disagreement
    is the client's only way to know."""
    cq_returns(TRAJECTORY_PAYLOAD)
    got = people_client.get(f"/v1/people/{USER}/ent-suresh").json()
    series = got["trajectory"]["series"]
    assert [s["origin_id"] for s in series] == ["MTG-9", "MTG-8", "MTG-5", "MTG-3", "MTG-1"]
    assert [s["sequence"] for s in series] == [1, 2, 3, 4, 5]
    # The order is not recoverable from any value field, so a sorter cannot
    # coincide with the truth: assert against every sort a middlebox might do.
    for field in ("numerator", "denominator"):
        values = [s[field] for s in series]
        assert values != sorted(values), f"fixture is degenerate: sorted by {field}"
        assert values != sorted(values, reverse=True)
    assert [s["numerator"] for s in series] == [0, 1, 0, 2, 2]
    assert [s["denominator"] for s in series] == [2, 4, 1, 3, 4]


def test_nested_objects_and_nested_string_arrays_are_not_flattened(people_client, cq_returns):
    """series[] holds objects; earlier/recent hold arrays of strings inside
    objects. A flattening proxy turns either leaf into a scalar or hoists
    its keys up a level, and both render as a plausible-looking card."""
    cq_returns(TRAJECTORY_PAYLOAD)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert all(isinstance(s, dict) for s in traj["series"])
    assert traj["series"][2] == {"origin_id": "MTG-5", "sequence": 3,
                                "numerator": 0, "denominator": 1}
    for side in ("earlier", "recent"):
        assert isinstance(traj[side], dict)
        assert isinstance(traj[side]["origin_ids"], list)
        assert all(isinstance(o, str) for o in traj[side]["origin_ids"])
    assert traj["earlier"]["origin_ids"] == ["MTG-9", "MTG-8", "MTG-7"]
    assert traj["recent"]["origin_ids"] == ["MTG-3", "MTG-2", "MTG-1"]


def test_no_float_anywhere_in_trajectory(people_client, cq_returns):
    """CQ's rule: no rate is ever served pre-divided, so the payload is all
    integers and GP's NaN walker has zero surface on it. Pinned as an
    assertion rather than an assumption, because the day a float appears is
    the day the walker could turn it into null and nobody would look."""
    cq_returns(TRAJECTORY_PAYLOAD)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert _floats_in(traj) == []
    for value in (traj["span_meetings"], traj["display_order"],
                  traj["earlier"]["numerator"], traj["recent"]["denominator"],
                  traj["earlier"]["meetings"]):
        assert type(value) is int
    for element in traj["series"]:
        for field in ("sequence", "numerator", "denominator"):
            assert type(element[field]) is int, (
                f"series {field} came back as {type(element[field]).__name__}")
    # The guard on the guard: the walker itself finds a float when there is
    # one, so an empty list above means "none present", not "never looked".
    assert _floats_in({"a": [{"b": 1.5}]}) == ["trajectory.a[0].b"]


def test_supersedes_empty_list_is_not_null_and_not_absent(people_client, cq_returns):
    """Three states the client renders differently: [] is "nothing was
    superseded", null is "CQ does not track that here", absent is a bug."""
    cq_returns(TRAJECTORY_PAYLOAD)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert "supersedes" in traj
    assert traj["supersedes"] == []
    assert traj["supersedes"] is not None


@pytest.mark.parametrize("field", ["supersedes", "series"])
def test_empty_list_survives_as_empty_list(people_client, cq_returns, field):
    payload = json.loads(json.dumps(TRAJECTORY_PAYLOAD))
    payload["trajectory"][field] = []
    cq_returns(payload)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert field in traj, f"{field} went absent when it was empty"
    assert traj[field] == [] and traj[field] is not None


@pytest.mark.parametrize("field", ["supersedes", "series"])
def test_null_list_survives_as_null_not_empty(people_client, cq_returns, field):
    """The other direction of the same distinction: a null must not be
    helpfully normalised into an empty list on the way through."""
    payload = json.loads(json.dumps(TRAJECTORY_PAYLOAD))
    payload["trajectory"][field] = None
    cq_returns(payload)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert field in traj and traj[field] is None


def test_person_detail_query_string_reaches_cq_verbatim(people_client, monkeypatch):
    """The whole of the request side of this hop. There is no selector param
    today; this is what proves one would arrive if CQ ever adds it."""
    monkeypatch.setattr(
        cq_proxy, "get_settings",
        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    recorder = _cq_recording(TRAJECTORY_PAYLOAD)
    monkeypatch.setattr(cq_proxy.httpx, "AsyncClient", lambda *a, **k: recorder)

    query = "include=trajectory&since=2026-06-01&delta=true"
    resp = people_client.get(f"/v1/people/{USER}/ent-suresh?{query}")
    assert resp.status_code == 200

    method, url = recorder.request.await_args.args
    assert method == "GET"
    assert url == f"/v1/people/{USER}/ent-suresh?{query}", (
        "the query string was rewritten or dropped on the way up")


# --- pair_kind / counted_noun (CQ contract amendment, 2026-08-22) ----------
#
# CQ served both kinds of pair as bare numerator/denominator, so a RATE
# reached a prompt as "214 out of 8". Both writer models in their selection
# eval refused every rate case as mathematically impossible and were right;
# the eval was measuring the defect and reporting it as model quality. SS hit
# the same conflation from the rendering end within the hour, a rate pinned
# flat against the top of a 0..1 axis. Two ends of one bug, neither able to
# see the other's half.
#
# So `pair_kind` is not decoration, it is the field that stops a rate being
# read as a proportion, and `counted_noun` is what makes the sentence sayable.
# Losing either one on this hop reproduces CQ's bug downstream of CQ's fix,
# silently, because 214/8 stays a perfectly well formed pair of integers.
#
# The proportion case lives in TRAJECTORY above. This is the rate case, and
# it is the one where numerator > denominator is CORRECT and any helpful
# normalisation is the bug.
TRAJECTORY_RATE = {
    "lens": "how_theyre_changing",
    "display_order": 5,
    "measure_key": "speaking_turns",
    "pair_kind": "rate",
    "counted_noun": "speaking turns",
    "subject": "Suresh",
    "unit": "speaking turns per meeting",
    "valence": "neutral",
    "movement": "up",
    "direction": "up",
    "span_meetings": 8,
    # 214 turns ACROSS 8 meetings. 214 is not a share of 8.
    "earlier": {"numerator": 71, "denominator": 3, "meetings": 3,
                "origin_ids": ["MTG-9", "MTG-8", "MTG-7"]},
    "recent": {"numerator": 143, "denominator": 5, "meetings": 5,
               "origin_ids": ["MTG-3", "MTG-2", "MTG-1"]},
    "series": [
        {"origin_id": "MTG-9", "sequence": 1, "numerator": 31, "denominator": 1},
        {"origin_id": "MTG-8", "sequence": 2, "numerator": 18, "denominator": 1},
        {"origin_id": "MTG-3", "sequence": 3, "numerator": 44, "denominator": 1},
        {"origin_id": "MTG-1", "sequence": 4, "numerator": 29, "denominator": 1},
    ],
    "supersedes": [],
    "about_person": "ent-suresh",
    "text": "He is taking more of the room than he used to.",
    "narrative": "143 speaking turns across your last five meetings, up from 71 across three.",
    "do": "Leave the first two minutes to him and see what he opens with.",
}

RATE_PAYLOAD = {
    "entity_id": "ent-suresh",
    "display_name": "Suresh",
    "trajectory": TRAJECTORY_RATE,
    "insights": [],
}


@pytest.mark.parametrize("payload,kind,noun", [
    (TRAJECTORY_PAYLOAD, "proportion", "items"),
    (RATE_PAYLOAD, "rate", "speaking turns"),
])
def test_pair_kind_and_counted_noun_arrive(people_client, cq_returns, payload, kind, noun):
    """Both are required strings on CQ's side, so absent is never a legal
    state here: absent means this hop ate it."""
    cq_returns(payload)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert "pair_kind" in traj, "pair_kind was dropped; a rate now reads as a proportion"
    assert "counted_noun" in traj, "counted_noun was dropped"
    assert traj["pair_kind"] == kind
    assert traj["counted_noun"] == noun
    assert isinstance(traj["pair_kind"], str) and isinstance(traj["counted_noun"], str)


def test_rate_trajectory_arrives_value_for_value(people_client, cq_returns):
    cq_returns(RATE_PAYLOAD)
    got = people_client.get(f"/v1/people/{USER}/ent-suresh").json()
    assert got["trajectory"] == TRAJECTORY_RATE


def test_a_rate_keeps_a_numerator_larger_than_its_denominator(people_client, cq_returns):
    """The assertion that would have caught CQ's bug if it had lived on this
    hop instead of theirs. 214 across 8 is correct for a rate, so anything
    that clamps, swaps or "fixes" the pair into 0..1 is destroying data that
    looks wrong and is not. Asserted on the aggregates AND per series
    element, since a normaliser would plausibly only touch one."""
    cq_returns(RATE_PAYLOAD)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    for side in ("earlier", "recent"):
        pair = traj[side]
        assert pair["numerator"] > pair["denominator"], (
            f"{side} was normalised; a rate is not a share of anything")
        assert type(pair["numerator"]) is int and type(pair["denominator"]) is int
    assert (traj["earlier"]["numerator"], traj["earlier"]["denominator"]) == (71, 3)
    assert (traj["recent"]["numerator"], traj["recent"]["denominator"]) == (143, 5)
    for element in traj["series"]:
        assert element["numerator"] > element["denominator"]


def test_the_two_kinds_are_not_interchangeable_across_the_hop(people_client, cq_returns):
    """Belt and braces on the thing that actually matters: serve a rate, and
    what comes back must still SAY rate. A hop that carried the numbers
    faithfully but flipped this one string would put "8 of 214" in a prompt
    and nothing downstream could tell."""
    cq_returns(RATE_PAYLOAD)
    rate = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    cq_returns(TRAJECTORY_PAYLOAD)
    prop = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert rate["pair_kind"] == "rate" and prop["pair_kind"] == "proportion"
    assert rate["pair_kind"] != prop["pair_kind"]
    assert rate["counted_noun"] != prop["counted_noun"]


def test_no_float_anywhere_in_a_rate_trajectory(people_client, cq_returns):
    """The rate is the shape most likely to tempt someone into serving a
    pre-divided 26.75, which is exactly what CQ's rule forbids."""
    cq_returns(RATE_PAYLOAD)
    traj = people_client.get(f"/v1/people/{USER}/ent-suresh").json()["trajectory"]
    assert _floats_in(traj) == []
