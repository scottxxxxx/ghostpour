"""Alignment Layer proxy (CQ #20, 2026-08-23).

Four routes, all app-authenticated like People. What the contract makes
load bearing, and what these pin:

- the 409 and 422 BODIES are the contract (the picker lesson: a middlebox
  that drops 4xx bodies breaks the client), so they must cross unchanged;
- every array keeps its order (supersedes, impact, evidence, history);
- the two POST bodies reach CQ verbatim, which is the request-side half
  rule 3 asks for on a POST and cannot see on a GET;
- the caller can only reach their own data, and the entitlement toggle
  closes the door for real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-align-1"


def _user(user_id=USER, tier="free"):
    return UserRecord(id=user_id, apple_sub="sub_align", tier=tier,
                      created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")


@pytest.fixture
def align_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def other_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user("someone-else")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _cq(payload, status=200):
    """An httpx.AsyncClient stub that records the request and answers."""
    resp = MagicMock(); resp.status_code = status; resp.json.return_value = payload; resp.text = ""
    inst = AsyncMock()
    inst.__aenter__ = AsyncMock(return_value=inst); inst.__aexit__ = AsyncMock(return_value=False)
    inst.request = AsyncMock(return_value=resp)
    return inst


@pytest.fixture
def cq_returns(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings", lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    holder = {}
    def _install(payload, status=200):
        holder["inst"] = _cq(payload, status)
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient", lambda *a, **k: holder["inst"])
        return holder["inst"]
    return _install


# Shapes from CQ's contract (docs/architecture/20-alignment-layer.md),
# with every array deliberately NOT in sorted order so a sorting middlebox
# cannot pass by coincidence.
PROJECT_RECORD = {
    "project_id": "proj-1",
    "current_directions": [
        {"event_id": "ev-9", "statement": "Ship in two phases", "decided_at": "2026-08-20T10:00:00Z",
         "supersedes": ["ev-3", "ev-1", "ev-2"],
         "impact": [{"kind": "timeline", "delta_days": 14}, {"kind": "scope", "delta_days": -3}],
         "evidence": [{"origin_id": "MTG-7", "quote": "let's split it"}, {"origin_id": "MTG-2", "quote": "one release"}]},
    ],
    "awaiting_confirmation": [{"event_id": "ev-11", "statement": "Drop the legacy importer", "proposed_by": "Priya"}],
    "history": [{"event_id": "ev-9"}, {"event_id": "ev-3"}, {"event_id": "ev-11"}, {"event_id": "ev-1"}],
    "direction_change_count": 3,
    "cumulative_impact": {"timeline_days": 11, "scope_items": -3},
    "definitions": {"direction": "a decision that changes what the project is doing"},
}


# --- response side: bodies and order ---------------------------------------

def test_project_record_arrives_with_every_array_in_order(align_client, cq_returns):
    cq_returns(PROJECT_RECORD)
    r = align_client.get(f"/v1/alignment/{USER}/projects/proj-1")
    assert r.status_code == 200
    got = r.json()
    assert got == PROJECT_RECORD
    d = got["current_directions"][0]
    assert d["supersedes"] == ["ev-3", "ev-1", "ev-2"]
    assert [i["kind"] for i in d["impact"]] == ["timeline", "scope"]
    assert [e["origin_id"] for e in d["evidence"]] == ["MTG-7", "MTG-2"]
    assert [h["event_id"] for h in got["history"]] == ["ev-9", "ev-3", "ev-11", "ev-1"]
    assert type(got["direction_change_count"]) is int


def test_an_empty_meeting_card_is_200_with_an_empty_list(align_client, cq_returns):
    """events: [] means no card. It must arrive as an empty list, not be
    dropped, and not become a 404, so the client can tell "nothing
    happened" from "the route is missing"."""
    cq_returns({"origin_id": "MTG-1", "events": []})
    r = align_client.get(f"/v1/alignment/{USER}/meetings/MTG-1")
    assert r.status_code == 200
    assert r.json()["events"] == []


@pytest.mark.parametrize("status,body", [
    (409, {"code": "NOT_CONFIRMABLE", "message": "already superseded", "event_id": "ev-3"}),
    (422, {"code": "SHARED_TEXT_REJECTED", "term": "final", "message": "statement reuses shared text"}),
    (409, {"code": "CORRECTION_CONFLICT",
           "existing": {"statement": "Ship in two phases", "corrected_by": "Marcus"},
           "proposed": {"statement": "Ship in one phase", "corrected_by": "Priya"}}),
])
def test_4xx_bodies_cross_unchanged_because_they_are_the_contract(align_client, cq_returns, status, body):
    """The picker lesson: a middlebox that drops 4xx bodies breaks the
    client. Status AND body, element for element."""
    cq_returns(body, status)
    r = align_client.post(f"/v1/alignment/{USER}/events/ev-3/correct",
                          json={"statement": "x", "reason": "y", "corrected_by": "me"})
    assert r.status_code == status
    assert r.json() == body


# --- request side: the POST bodies -----------------------------------------

def test_confirm_body_reaches_cq_verbatim_on_the_right_path(align_client, cq_returns):
    inst = cq_returns({"ok": True})
    body = {"confirmed_by": "Priya", "on_behalf": False}
    r = align_client.post(f"/v1/alignment/{USER}/events/ev-11/confirm", json=body)
    assert r.status_code == 200
    method, path = inst.request.await_args.args
    assert method == "POST"
    assert path == f"/v1/alignment/{USER}/events/ev-11/confirm"
    assert inst.request.await_args.kwargs["json"] == body
    assert inst.request.await_args.kwargs["json"]["on_behalf"] is False


def test_correct_body_reaches_cq_verbatim_including_the_optional_rationale(align_client, cq_returns):
    inst = cq_returns({"ok": True})
    body = {"statement": "Ship in one phase", "reason": "scope shrank",
            "corrected_by": "Marcus", "rationale": "the second phase has no owner"}
    align_client.post(f"/v1/alignment/{USER}/events/ev-9/correct", json=body)
    assert inst.request.await_args.kwargs["json"] == body


def test_a_key_cq_adds_later_to_a_post_body_is_not_modelled_away(align_client, cq_returns):
    """The to_name shape on this hop: the POST bodies are `dict`, not a
    model, so a field CQ adds next month crosses without a GP deploy."""
    inst = cq_returns({"ok": True})
    body = {"confirmed_by": "Priya", "on_behalf": True, "future_field": {"deep": [1, None]}}
    align_client.post(f"/v1/alignment/{USER}/events/ev-11/confirm", json=body)
    assert inst.request.await_args.kwargs["json"]["future_field"] == {"deep": [1, None]}


def test_get_routes_map_to_the_right_cq_paths(align_client, cq_returns):
    inst = cq_returns({})
    align_client.get(f"/v1/alignment/{USER}/meetings/MTG-7?since=2026-08-01")
    method, path = inst.request.await_args.args
    assert (method, path) == ("GET", f"/v1/alignment/{USER}/meetings/MTG-7?since=2026-08-01")


# --- ownership and the gate ------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", f"/v1/alignment/{USER}/meetings/MTG-1"),
    ("get", f"/v1/alignment/{USER}/projects/proj-1"),
    ("post", f"/v1/alignment/{USER}/events/ev-1/confirm"),
    ("post", f"/v1/alignment/{USER}/events/ev-1/correct"),
])
def test_another_user_cannot_reach_this_users_alignment(other_client, cq_returns, method, path):
    inst = cq_returns({"leak": True})
    r = other_client.post(path, json={}) if method == "post" else other_client.get(path)
    assert r.status_code == 403
    assert not inst.request.called, "the request reached CQ despite the 403"


def test_the_entitlement_toggle_closes_the_door(align_client, cq_returns):
    """Enabled on every tier today, checked anyway so the dashboard switch
    is real rather than decorative."""
    inst = cq_returns(PROJECT_RECORD)
    matrix = app.state.remote_configs["entitlements"]["matrix"]
    original = matrix.get("alignment")
    matrix["alignment"] = {**(original or {}), "free": "disabled"}
    try:
        r = align_client.get(f"/v1/alignment/{USER}/projects/proj-1")
        assert r.status_code == 403
        assert r.json()["detail"]["feature"] == "alignment"
        assert not inst.request.called
    finally:
        if original is None: matrix.pop("alignment", None)
        else: matrix["alignment"] = original


def test_alignment_is_enabled_on_every_tier_in_the_served_matrix():
    d = json.load(open("config/remote/entitlements.json"))
    cells = d["matrix"]["alignment"]
    assert set(cells) == {"free", "plus", "pro", "admin", "automation"}
    assert set(cells.values()) == {"enabled"}
