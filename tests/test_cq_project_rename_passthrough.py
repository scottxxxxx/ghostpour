"""PATCH /v1/projects/{user}/{project} is carried, both directions verbatim.

SS has sent this on every project rename since at least 2026-07-31 and GP
never had the route: seven 404s in the proxy log between 07-26 and 08-21,
each discarded by the client as a silent false, so no side had a signal
(rule 1, three coherent local pictures). These tests are the receipt that
the verb now exists, that the body SS sends is what CQ receives, and that
CQ's status and body are what SS gets back, so SS can compare the
returned name to the one it sent instead of trusting a 200.
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

USER = "user-rename-1"
PROJECT = "B0A59EAA-ADC3-41AE-BB56-C14A3B3B7594"   # the id from the 08-20 404
SENT = {"name": "Sunset Canyon"}
CQ_200 = {"project_id": PROJECT, "name": "Sunset Canyon", "status": "active",
          "updated_at": "2026-08-21T09:30:00.000000+00:00"}
CQ_404 = {"detail": "Project not found"}


def _user(user_id: str = USER) -> UserRecord:
    return UserRecord(id=user_id, apple_sub="sub_rename", tier="pro",
                      created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")


@pytest.fixture
def as_user(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def cq(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings",
                        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    handle = SimpleNamespace(instance=None)

    def _install(payload, status=200):
        resp = MagicMock(); resp.status_code = status
        resp.json.return_value = payload; resp.text = json.dumps(payload)
        inst = AsyncMock()
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        inst.request = AsyncMock(return_value=resp)
        handle.instance = inst
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient", lambda *a, **k: inst)
        return handle
    handle.install = _install
    return handle


def _outbound(handle):
    call = handle.instance.request.await_args
    return call.args[0], call.args[1], call.kwargs.get("json")


def test_the_route_exists_and_the_body_reaches_cq_verbatim(as_user, cq):
    """The whole bug: this used to be a 404 before any of the below could
    matter."""
    cq.install(CQ_200)
    resp = as_user.patch(f"/v1/projects/{USER}/{PROJECT}", json=SENT)
    assert resp.status_code != 404, "the verb SS ships still has no handler"
    method, path, sent = _outbound(cq)
    assert method == "PATCH"
    assert path.endswith(f"/projects/{USER}/{PROJECT}") and "/v1/projects/" in path
    assert sent == SENT


def test_cqs_200_and_its_body_come_back_unchanged(as_user, cq):
    """Check the echo (rule 4): SS compares the returned name to the one it
    sent, which only works if the body survives the hop."""
    cq.install(CQ_200)
    resp = as_user.patch(f"/v1/projects/{USER}/{PROJECT}", json=SENT)
    assert resp.status_code == 200
    assert json.loads(resp.content) == CQ_200


def test_a_404_from_cq_is_a_404_with_the_body_intact(as_user, cq):
    """Distinguishable from the old gateway 404: CQ's detail travels."""
    cq.install(CQ_404, status=404)
    resp = as_user.patch(f"/v1/projects/{USER}/{PROJECT}", json=SENT)
    assert resp.status_code == 404
    assert json.loads(resp.content) == CQ_404


def test_archive_on_the_same_verb_passes_through(as_user, cq):
    body = {"status": "archived"}
    cq.install({**CQ_200, "status": "archived"})
    resp = as_user.patch(f"/v1/projects/{USER}/{PROJECT}", json=body)
    assert resp.status_code == 200
    assert _outbound(cq)[2] == body


def test_another_users_project_is_refused_before_cq_is_called(as_user, cq):
    cq.install(CQ_200)
    resp = as_user.patch(f"/v1/projects/someone-else/{PROJECT}", json=SENT)
    assert resp.status_code == 403
    assert cq.instance.request.await_count == 0


# --- Siblings from SS's QuiltService inventory (2026-08-21) ----------------

@pytest.mark.parametrize("path,upstream_tail", [
    (f"/v1/projects/{USER}/{PROJECT}/unscope", f"/projects/{USER}/{PROJECT}/unscope"),
    (f"/v1/origins/{USER}/meeting/{PROJECT}/unassign-project", f"/origins/{USER}/meeting/{PROJECT}/unassign-project"),
])
def test_sibling_verbs_are_carried_with_body_and_status_verbatim(as_user, cq, path, upstream_tail):
    cq.install({"ok": True, "project_id": PROJECT})
    body = {"reason": "moved"}
    resp = as_user.post(path, json=body)
    assert resp.status_code == 200, path
    method, upstream, sent = _outbound(cq)
    assert method == "POST" and upstream.endswith(upstream_tail)
    assert sent == body
    assert json.loads(resp.content) == {"ok": True, "project_id": PROJECT}


@pytest.mark.parametrize("path", [
    f"/v1/projects/other/{PROJECT}/unscope",
    f"/v1/origins/other/meeting/{PROJECT}/unassign-project",
])
def test_sibling_verbs_guard_ownership(as_user, cq, path):
    cq.install({"ok": True})
    assert as_user.post(path, json={}).status_code == 403
    assert cq.instance.request.await_count == 0
