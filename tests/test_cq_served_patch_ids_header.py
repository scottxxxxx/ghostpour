"""CQ #481: `served_patch_ids` reaches SS as `X-CQ-Served-Patch-IDs`.

CQ serves `served_patch_ids` on /v1/recall beside `matched_patch_ids`. GP
consumes recall inside the chat pipeline, so the field reaches SS only if GP
maps it to a header. Rule 3: CQ sees a complete response and SS sees a
missing header, so neither endpoint can see a drop on this hop. These tests
feed a CQ-shaped body at the httpx boundary (the last place it is CQ's) and
read the header off the REAL chat route, JSON and SSE.

Contract (CQ doc 25, 2026-09-13):
- X-CQ-Patch-IDs keeps carrying matched_patch_ids, byte-identical, because SS
  reads it today.
- served_patch_ids absent (a CQ build before #481) means NO served header,
  never a GP-side fallback to the candidate list.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import context_quilt as cq
from app.services.features.context_quilt_hook import ContextQuiltHook

# Deliberately DIFFERENT lists: a served list is a subset of the candidates in
# a different order, so a header carrying the wrong one cannot pass.
MATCHED = ["p-cand-1", "p-cand-2", "p-cand-3", "p-cand-4"]
SERVED = ["p-cand-3", "p-cand-1"]

SEND = {
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "system_prompt": "BASE\n\n{{context_quilt}}\n\nNOTES",
    "user_content": "what did we decide about the routing item?",
    "context_quilt": True,
    "metadata": {"prompt_mode": "PostMeetingChat"},
}


def _cq_body(**overrides) -> dict:
    body = {
        "context": "Met with Bob last Tuesday about routing.",
        "matched_entities": ["Bob Martinez"],
        "matched_patch_ids": MATCHED,
        "served_patch_ids": SERVED,
        "patch_count": 4,
    }
    body.update(overrides)
    return body


@pytest.fixture
def cq_wire(request):
    body = getattr(request, "param", None) or _cq_body()
    post = AsyncMock()
    post.return_value = type("R", (), {
        "status_code": 200,
        "json": lambda self: body,
        "raise_for_status": lambda self: None})()
    http_client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        yield post


def _chat(client, user, **extra):
    resp = client.post("/v1/chat", json={**SEND, **extra}, headers=user["headers"])
    assert resp.status_code == 200, resp.text
    return resp


def test_json_route_carries_served_ids_in_served_order(client, pro_user, cq_wire):
    resp = _chat(client, pro_user)
    assert resp.headers.get("x-cq-served-patch-ids") == "p-cand-3,p-cand-1"


def test_candidate_header_is_unchanged_beside_it(client, pro_user, cq_wire):
    resp = _chat(client, pro_user)
    assert resp.headers.get("x-cq-patch-ids") == ",".join(MATCHED)


def test_stream_route_carries_served_ids(client, pro_user, cq_wire):
    resp = _chat(client, pro_user, stream=True)
    assert resp.headers.get("x-cq-served-patch-ids") == "p-cand-3,p-cand-1"


@pytest.mark.parametrize("cq_wire", [_cq_body(served_patch_ids=None)], indirect=True)
def test_cq_before_481_sends_no_served_header_and_no_fallback(client, pro_user, cq_wire):
    resp = _chat(client, pro_user)
    assert "x-cq-served-patch-ids" not in resp.headers
    assert resp.headers.get("x-cq-patch-ids") == ",".join(MATCHED)


def test_hook_absent_key_is_not_a_fallback():
    """The route test above feeds `None`; this one feeds a body with the key
    genuinely ABSENT, the actual shape of a pre-#481 CQ."""
    body = _cq_body()
    del body["served_patch_ids"]
    headers = ContextQuiltHook().response_headers({"cq_result": body}, "enabled")
    assert "X-CQ-Served-Patch-IDs" not in headers
    assert headers["X-CQ-Patch-IDs"] == ",".join(MATCHED)


def test_gated_leg_carries_served_ids_too():
    headers = ContextQuiltHook().response_headers(
        {"cq_result": _cq_body(), "gated": True}, "teaser")
    assert headers["X-CQ-Gated"] == "true"
    assert headers["X-CQ-Served-Patch-IDs"] == "p-cand-3,p-cand-1"


def test_served_header_caps_at_twenty_like_its_sibling():
    served = [f"s-{i}" for i in range(30)]
    candidates = [f"c-{i}" for i in range(40)]
    headers = ContextQuiltHook().response_headers(
        {"cq_result": _cq_body(served_patch_ids=served, matched_patch_ids=candidates)}, "enabled")
    assert headers["X-CQ-Served-Patch-IDs"].split(",") == served[:20]
