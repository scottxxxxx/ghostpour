"""POST /v1/origins/{user}/{type}/{id}/replace-term is carried verbatim.

CQ #499 is the CQ half of SS's "fix a misheard word once, everywhere it
landed". The hop risks are request side (rule 3): `from` is a Python keyword
and would need an alias in any typed schema, and an absent `match_case`
means false at CQ, so dropping it flips a case sensitive fix silently. And
response side (rule 4): SS compares CQ's echo to its own count, so the body
must survive whole, including CQ's 422 detail.
"""
from __future__ import annotations

import json

import pytest

from tests.test_cq_project_rename_passthrough import _outbound, as_user, cq  # noqa: F401

USER = "user-rename-1"
MEETING = "A8E8E433-622C-495B-8FF1-54560D85D9B0"
PATH = f"/v1/origins/{USER}/meeting/{MEETING}/replace-term"
ECHO = {
    "matches": 7, "patches_changed": 3, "patch_ids": ["p1", "p2", "p3"],
    "cues_changed": 2,
    "entities_renamed": [{"entity_id": "e1", "from": "Cora", "to": "Kore.ai"}],
    "entities_skipped": [{"entity_id": "e2", "reason": "shared_with_other_origin"}],
    "entities_skipped_shared": 1, "derived_referencing": 4,
    "limits": {"max_patches": 500, "truncated": False},
}


@pytest.mark.parametrize("body", [
    {"from": "Cora", "to": "Kore.ai", "match_case": False},
    {"from": "cora", "to": "Kore.ai", "match_case": True},
])
def test_all_three_keys_reach_cq_exactly(as_user, cq, body):
    cq.install(ECHO)
    resp = as_user.post(PATH, json=body)
    assert resp.status_code == 200
    method, upstream, sent = _outbound(cq)
    assert method == "POST"
    assert upstream.endswith(f"/v1/origins/{USER}/meeting/{MEETING}/replace-term")
    assert sent == body
    assert sent["match_case"] is body["match_case"]


def test_the_echo_comes_back_whole(as_user, cq):
    cq.install(ECHO)
    resp = as_user.post(PATH, json={"from": "Cora", "to": "Kore.ai", "match_case": False})
    assert json.loads(resp.content) == ECHO


def test_cqs_422_detail_passes_through(as_user, cq):
    detail = {"detail": {"code": "empty_term", "message": "from and to must be non-empty"}}
    cq.install(detail, status=422)
    resp = as_user.post(PATH, json={"from": "", "to": "Kore.ai", "match_case": False})
    assert resp.status_code == 422
    assert json.loads(resp.content) == detail


def test_another_users_origin_is_refused_before_cq_is_called(as_user, cq):
    cq.install(ECHO)
    resp = as_user.post(f"/v1/origins/someone-else/meeting/{MEETING}/replace-term",
                        json={"from": "Cora", "to": "Kore.ai", "match_case": False})
    assert resp.status_code == 403
    assert cq.instance.request.await_count == 0
