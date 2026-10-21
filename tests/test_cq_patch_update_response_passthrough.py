"""A field CQ ADDS to the PATCH /patches/{id} response must reach the client.

The request side of this route is pinned (test_cq_proxy_passthrough.py). The
response side was not: `_cq_proxy` hands back CQ's parsed body and nothing
in the file models it, so new keys crossed only because nobody had typed a
model yet. That is passthrough one refactor away from ending with a 200 and
a quietly shorter body.

The occasion (SS Dev, 2026-10-21): CQ #498 makes the edit return the stored
fact, patch_type, owner, project_id and permanence_override alongside
status, patch_id and deadline_date, so SS can compare its edit against the
echo per field. SS logs "echo absent" when a field is missing, which means
GP eating one would read on the device as CQ not storing it. GP has not read
CQ #498; the shape below is SS Dev's description of it.

A note for whoever sabotages this: adding `response_model=` to the decorator
does NOT strip anything here, because the handler returns a JSONResponse and
FastAPI skips response_model serialisation for a returned Response. The
failure this file guards is a handler that parses or rebuilds the body.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-patch-echo-1"
PATCH_ID = "477d19ff-1af1-4482-9904-d1dfd6d4ee1b"
URL = f"/v1/quilt/{USER}/patches/{PATCH_ID}"


def _user(user_id=USER, tier="free"):
    return UserRecord(id=user_id, apple_sub="sub_patch_echo", tier=tier,
                      created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")


@pytest.fixture
def patch_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _stub(payload, status=200):
    resp = MagicMock(); resp.status_code = status
    resp.json.return_value = payload; resp.text = ""
    inst = AsyncMock()
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    inst.request = AsyncMock(return_value=resp)
    return inst


@pytest.fixture
def cq_returns(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings",
                        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    holder = {}

    def _install(payload, status=200):
        holder["inst"] = _stub(payload, status)
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient",
                            lambda *a, **k: holder["inst"])
        return holder["inst"]
    return _install


# Today's keys plus the five #498 adds. Nulls are carried on purpose: a
# proxy that dropped null-valued keys would turn "CQ stored no project" into
# "echo absent" on the device, which SS reads as a different thing.
ECHO = {
    "status": "updated",
    "patch_id": PATCH_ID,
    "deadline_date": "2026-11-03",
    "fact": "Send the revised deck to Dana",
    "patch_type": "commitment",
    "owner": "user",
    "project_id": None,
    "permanence_override": None,
}


def test_the_edit_echo_crosses_byte_identical(patch_client, cq_returns):
    inst = cq_returns(ECHO)
    r = patch_client.patch(URL, json={"text": "Send the revised deck to Dana"})
    assert r.status_code == 200
    assert r.json() == ECHO
    method, path = inst.request.call_args.args[:2]
    assert method == "PATCH" and path.endswith(f"/patches/{PATCH_ID}")


@pytest.mark.parametrize("key", ["fact", "patch_type", "owner",
                                 "project_id", "permanence_override"])
def test_each_new_echo_field_survives_by_name(patch_client, cq_returns, key):
    """Named one by one so a failure says WHICH field the hop ate."""
    cq_returns(ECHO)
    got = patch_client.patch(URL, json={"text": "x"}).json()
    assert key in got, f"GP DROPPED {key} from the patch edit echo"
    assert got[key] == ECHO[key]


def test_a_field_nobody_has_invented_yet_also_survives(patch_client, cq_returns):
    payload = {**ECHO, "a_field_gp_has_never_heard_of": {"nested": ["and", "ordered"]}}
    cq_returns(payload)
    got = patch_client.patch(URL, json={"text": "x"}).json()
    assert got["a_field_gp_has_never_heard_of"] == {"nested": ["and", "ordered"]}


def test_an_error_body_from_cq_crosses_intact(patch_client, cq_returns):
    cq_returns({"detail": {"code": "patch_not_found", "patch_id": PATCH_ID}}, status=404)
    r = patch_client.patch(URL, json={"text": "x"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "patch_not_found"
