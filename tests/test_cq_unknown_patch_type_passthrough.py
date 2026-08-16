"""An unknown `patch_type` must reach the client verbatim.

CQ is registering a `behavior` patch type (their #243). Under the
additive contract it starts appearing in `/v1/quilt` sync with no route
change and nothing for GP to wire, which is exactly the shape where a
gateway quietly eats a key and the partner spends a day looking at their
own code. The people routes got this proof in #674; the quilt route,
which is the one `behavior` actually rides, did not have it.

These run the REAL `_cq_proxy` with only CQ's HTTP response stubbed. The
property under test is that GP has no patch_type vocabulary at all: it
never enumerates, filters, or reshapes, so a type invented after this
test was written passes through untouched.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-quilt-1"


def _user(user_id: str = USER, tier: str = "free") -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_quilt", tier=tier,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def quilt_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _cq_answers(payload, status=200):
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
    monkeypatch.setattr(
        cq_proxy, "get_settings",
        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))

    def _install(payload, status=200):
        monkeypatch.setattr(
            cq_proxy.httpx, "AsyncClient",
            lambda *a, **k: _cq_answers(payload, status))
    return _install


# A behavior patch as CQ described it in #243: facet Episode, permanence
# quarter, not completable, not project scoped. Every field here is one
# GP has never heard of, which is the point.
BEHAVIOR_PATCH = {
    "patch_id": "p-behav-1",
    "patch_type": "behavior",
    "fact": "Restates the ask in their own words before agreeing",
    "facet": "Episode",
    "permanence": "quarter",
    "completable": False,
    "project_scoped": False,
    "entity_id": "ent-9",
    "created_at": "2026-08-16T10:00:00Z",
    "observation": {"count": 4, "spread_days": 21, "baseline_delta": 2.75},
}

QUILT_PAYLOAD = {
    "patches": [
        {"patch_id": "p-1", "patch_type": "decision",
         "fact": "Ship the queue change", "created_at": "2026-08-01T09:00:00Z"},
        BEHAVIOR_PATCH,
        # A type that does not exist anywhere yet. If the test only proved
        # "behavior" survives, it would be proving a hardcoded allowance.
        {"patch_id": "p-future", "patch_type": "not_invented_yet",
         "fact": "whatever CQ ships next", "nested": {"deep": [1, None, "x"]}},
    ],
    "server_time": "2026-08-16T12:00:00Z",
    "counts": {"total": 3},
}


def test_unknown_patch_types_reach_the_client_verbatim(quilt_client, cq_returns):
    cq_returns(QUILT_PAYLOAD)

    resp = quilt_client.get(f"/v1/quilt/{USER}")

    assert resp.status_code == 200
    assert json.loads(resp.content) == QUILT_PAYLOAD

    # Named explicitly so a future reshaping fails with a readable diff.
    got = resp.json()["patches"][1]
    assert got["patch_type"] == "behavior"
    assert got["facet"] == "Episode"
    assert got["permanence"] == "quarter"
    assert got["completable"] is False
    assert got["project_scoped"] is False
    assert got["observation"]["baseline_delta"] == 2.75
    assert resp.json()["patches"][2]["patch_type"] == "not_invented_yet"


def test_a_non_finite_number_is_the_one_thing_we_do_change(
        quilt_client, cq_returns, caplog):
    """Full disclosure for CQ: there IS exactly one transform on this
    path. A bare NaN/Infinity parses out of CQ's JSON but cannot be
    re-rendered, and one of them used to 502 the whole response, so we
    null it and log loudly. A behavior patch carrying a non-finite
    number arrives with that number as null, everything else intact."""
    payload = {"patches": [dict(BEHAVIOR_PATCH,
                                observation={"baseline_delta": float("nan")})]}
    cq_returns(payload)

    with caplog.at_level("WARNING"):
        resp = quilt_client.get(f"/v1/quilt/{USER}")

    assert resp.status_code == 200
    got = resp.json()["patches"][0]
    assert got["observation"]["baseline_delta"] is None
    assert got["patch_type"] == "behavior"
    assert got["facet"] == "Episode"
    assert "cq_proxy_non_finite_float" in caplog.text


def test_the_recall_block_labels_an_unknown_type_verbatim():
    """The other path a patch takes: into prompt context. `_format_patch`
    reads patch_type as an opaque label with a category fallback, so a
    new type is rendered, not dropped and not relabelled 'fact'."""
    from app.services.context_quilt import _format_patch

    line = _format_patch(BEHAVIOR_PATCH)
    assert line.startswith("[behavior] ")
    assert "Restates the ask in their own words" in line

    # The fallback chain still holds for a patch with no type at all.
    assert _format_patch({"category": "note", "fact": "x"}) == "[note] x"
    assert _format_patch({"fact": "x"}) == "[fact] x"


def test_gp_declares_no_patch_type_vocabulary():
    """The guarantee is the ABSENCE of code. If someone ever adds a
    patch_type enum, allowlist, or match statement to the proxy, this
    fails and they have to come read the docstring above first."""
    from pathlib import Path

    src = Path(cq_proxy.__file__).read_text()
    body = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    )
    for forbidden in ("PATCH_TYPES", "ALLOWED_PATCH_TYPES", "patch_type =="):
        assert forbidden not in body, (
            f"{forbidden} in cq_proxy: the quilt route must stay a pure "
            "passthrough with no patch_type vocabulary")
