"""origin delete: the body SS sends is the body CQ receives, byte for byte.

Scott ruled 2026-09-07 that deleting a meeting clears its transcript from
CQ's memory stream. CQ specified the endpoint and GP carries the route
ahead of it (CQ's side is not live yet), so every assertion here is about
what GP SENDS, not what CQ answers. Rule 3: a dropped or rewritten key on
this hop is invisible from both ends. SS sees a correct send, CQ sees a
well-formed request that happens to be a preview, and nothing errors.

The `delete` key is the one CQ asked for by name: it is the entire
difference between a warning and a deletion. A hop that drops it fails
safe and silent, which is the worst kind, because nobody would ever look.

Held unmerged until CQ's endpoint lands; the expected path and bodies get
re-pinned then to the bytes CQ actually receives.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-delete-1"
MEETING = "7E1C0B7A-5D2B-4F0C-9A4E-2B6F1D3C8A90"
APP = "techrehearsal"   # a namespaced app, so a raw user_id in the path is visible
ROUTE = f"/v1/origins/{USER}/meeting/{MEETING}/delete"
EXPECTED_PATH = f"/v1/origins/{APP}:{USER}/meeting/{MEETING}/delete"
HEADERS = {"X-App-ID": APP}

# CQ's reference response, for the one response-side test. GP does not
# validate it; the test only proves GP did not touch it.
CQ_REPLY = {
    "origin_id": MEETING,
    "applied": False,
    "patches": {"archived": 0, "would_archive": 12},
    "transcripts": {"matched": 1, "bytes": 48213, "deleted": 0, "irreversible": True},
}


@pytest.fixture
def as_user(client):
    app.dependency_overrides[get_current_user] = lambda: UserRecord(
        id=USER, apple_sub="sub_delete", tier="pro",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def cq(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings", lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = CQ_REPLY; resp.text = "{}"
    inst = AsyncMock(); inst.__aenter__ = AsyncMock(return_value=inst); inst.__aexit__ = AsyncMock(return_value=False)
    inst.request = AsyncMock(return_value=resp)
    monkeypatch.setattr(cq_proxy.httpx, "AsyncClient", lambda *a, **k: inst)
    return inst


def _sent(inst):
    """(method, full outbound path including any query, json body) as GP
    handed them to httpx. The forwarder folds `query` into the path string,
    so a body smuggled out as a query shows up here as a `?` in the path."""
    call = inst.request.await_args
    return call.args[0], call.args[1], call.kwargs.get("json")


# --- request side ------------------------------------------------------


def test_outbound_method_and_path_are_byte_exact(as_user, cq):
    resp = as_user.post(ROUTE, json={"delete": True}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    method, path, _ = _sent(cq)
    assert method == "POST"
    assert path == EXPECTED_PATH, f"sent {path!r}"


def test_delete_true_reaches_cq_with_the_key_intact(as_user, cq):
    resp = as_user.post(ROUTE, json={"delete": True}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    sent = _sent(cq)[2]
    assert sent == {"delete": True}, f"forwarded {sent!r}; CQ reads `delete` to decide"
    assert sent["delete"] is True


def test_preview_true_reaches_cq_as_sent(as_user, cq):
    as_user.post(ROUTE, json={"preview": True}, headers=HEADERS)
    assert _sent(cq)[2] == {"preview": True}


@pytest.mark.parametrize("send", [
    pytest.param({}, id="no-body"),
    pytest.param({"json": {}}, id="empty-object"),
])
def test_an_empty_body_is_not_turned_into_preview(as_user, cq, send):
    """Absent means preview is CQ's rule. GP inventing {"preview": true}
    here would be GP deciding not to delete, and a later CQ change to that
    default would be silently overridden on this hop."""
    resp = as_user.post(ROUTE, headers=HEADERS, **send)
    assert resp.status_code == 200, resp.text
    sent = _sent(cq)[2]
    assert sent in (None, {}), f"GP invented a body: {sent!r}"


def test_an_unrecognised_key_is_forwarded_not_stripped(as_user, cq):
    as_user.post(ROUTE, json={"nuke": True}, headers=HEADERS)
    assert _sent(cq)[2] == {"nuke": True}


def test_another_users_path_is_403_and_nothing_goes_out(as_user, cq):
    resp = as_user.post(f"/v1/origins/someone-else/meeting/{MEETING}/delete",
                        json={"delete": True}, headers=HEADERS)
    assert resp.status_code == 403, resp.text
    assert cq.request.await_count == 0, "an outbound call was made for another user's meeting"


# --- response side (one; not a substitute for the above) ----------------


def test_cq_200_body_passes_through_unchanged(as_user, cq):
    resp = as_user.post(ROUTE, json={"preview": True}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == CQ_REPLY
