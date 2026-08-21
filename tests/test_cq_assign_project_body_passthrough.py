"""assign-project: the body SS sends is the body CQ receives.

SS's QuiltService sends exactly {"project_id": ..., "project_name": ...}
(verbatim, 2026-08-21). GP's model knew `project` and not `project_name`,
so pydantic dropped the key, the forward carried project_id alone, and
CQ 422'd naming project_name on all seven calls between 07-23 and 08-05.
Rule 3: this is a request-side hole and only a request-side test on this
hop can see it.
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

USER = "user-assign-1"
MEETING = "1541062D-3A7C-4CF8-B23A-F4A766ECF7DC"   # from the 08-02 422
SS_BODY = {"project_id": "B0A59EAA-ADC3-41AE-BB56-C14A3B3B7594", "project_name": "Sunset Canyon"}


@pytest.fixture
def as_user(client):
    app.dependency_overrides[get_current_user] = lambda: UserRecord(
        id=USER, apple_sub="sub_assign", tier="pro",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def cq(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings", lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {"ok": True}; resp.text = "{}"
    inst = AsyncMock(); inst.__aenter__ = AsyncMock(return_value=inst); inst.__aexit__ = AsyncMock(return_value=False)
    inst.request = AsyncMock(return_value=resp)
    monkeypatch.setattr(cq_proxy.httpx, "AsyncClient", lambda *a, **k: inst)
    return inst


def _sent(inst):
    call = inst.request.await_args
    return call.args[0], call.args[1], call.kwargs.get("json")


@pytest.mark.parametrize("path,upstream_tail", [
    (f"/v1/meetings/{USER}/{MEETING}/assign-project", f"/origins/{USER}/meeting/{MEETING}/assign-project"),
    (f"/v1/origins/{USER}/meeting/{MEETING}/assign-project", f"/origins/{USER}/meeting/{MEETING}/assign-project"),
])
def test_ss_exact_body_reaches_cq_with_project_name_intact(as_user, cq, path, upstream_tail):
    resp = as_user.post(path, json=SS_BODY)
    assert resp.status_code == 200, resp.text
    method, upstream, sent = _sent(cq)
    assert method == "POST" and upstream.endswith(upstream_tail)
    assert sent == SS_BODY, f"forwarded {sent!r}; CQ requires project_name"


def test_legacy_project_key_still_maps_onto_project_name(as_user, cq):
    as_user.post(f"/v1/meetings/{USER}/{MEETING}/assign-project",
                 json={"project_id": "p-1", "project": "Old Spelling"})
    assert _sent(cq)[2] == {"project_id": "p-1", "project_name": "Old Spelling"}


def test_project_name_wins_when_both_are_sent(as_user, cq):
    as_user.post(f"/v1/meetings/{USER}/{MEETING}/assign-project",
                 json={"project_id": "p-1", "project": "old", "project_name": "new"})
    assert _sent(cq)[2]["project_name"] == "new"
