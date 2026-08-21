"""Plus recall window: `metadata.max_age_days` reaches CQ's /v1/recall.

Rule 3 with teeth: this hop has eaten metadata keys before, and CQ cannot
see it from their socket. So these tests drive the REAL chat route and
read the body at the httpx boundary to CQ, the last place it is ours.

Contract (CQ, 2026-08-21): int >= 1 applies a window; absent means no
window. So Plus (dial 30) must send 30, Pro (dial null) must send NOTHING
rather than a sentinel, and the value must come from the tier dial, never
from anything the client put in its own metadata.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services import context_quilt as cq
from app.services.recall_window import recall_max_age_days

SEND = {
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "system_prompt": "BASE\n\n{{context_quilt}}\n\nNOTES",
    "user_content": "what did we decide about the routing item?",
    "context_quilt": True,
    "metadata": {"prompt_mode": "ProjectChat", "project_id": "p-1",
                 # a client trying to widen its own window: must be ignored
                 "max_age_days": 9999},
}


@pytest.fixture
def cq_wire():
    """Patch the client recall() uses; capture the /v1/recall body."""
    post = AsyncMock()
    post.return_value = type("R", (), {
        "status_code": 200,
        "json": lambda self: {"context": "", "matched_entities": [], "patch_count": 0},
        "raise_for_status": lambda self: None})()
    http_client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        yield post


def _recall_body(post) -> dict:
    calls = [c for c in post.call_args_list if c.args and c.args[0] == "/v1/recall"]
    assert calls, "no POST /v1/recall reached CQ"
    return calls[-1].kwargs["json"]


def _chat(client, user):
    resp = client.post("/v1/chat", json=SEND, headers=user["headers"])
    assert resp.status_code == 200, resp.text
    return resp


def test_the_dial_resolves_per_tier_from_the_served_bundle(client):
    rc = client.app.state.remote_configs
    assert recall_max_age_days(rc, "plus") == 30
    assert recall_max_age_days(rc, "pro") is None
    assert recall_max_age_days(rc, "free") is None
    assert recall_max_age_days(rc, "no-such-tier") is None


def test_plus_sends_its_window_to_cq(client, plus_user, cq_wire):
    _chat(client, plus_user)
    body = _recall_body(cq_wire)
    md = body["metadata"]
    assert md["max_age_days"] == 30
    assert md["subscription_tier"] == "plus"


def test_pro_sends_no_window_key_at_all(client, pro_user, cq_wire):
    """Absent means no window on CQ's side. A null or 0 here would be a
    different claim and CQ says it tolerates it, but we do not rely on
    tolerance: unlimited sends nothing."""
    _chat(client, pro_user)
    md = _recall_body(cq_wire)["metadata"]
    assert "max_age_days" not in md


def test_the_client_cannot_set_its_own_window(client, plus_user, cq_wire):
    """SEND carries metadata.max_age_days=9999 from the client; the wire
    must carry the tier's 30. Same property as recall_scope and
    subscription_tier: entitlement-derived keys are set server-side after
    composition, never copied."""
    _chat(client, plus_user)
    assert _recall_body(cq_wire)["metadata"]["max_age_days"] == 30


def test_the_window_rides_the_teaser_leg_too(client, free_user, cq_wire):
    """Free is a teaser/people-scoped leg with no window dial; the key must
    be absent there as well, not leaked from another tier's resolution."""
    _chat(client, free_user)
    md = _recall_body(cq_wire)["metadata"]
    assert "max_age_days" not in md


# --- Served copy is templated from the same dial ---------------------------

from app.services.recall_window import PLACEHOLDER, render_recall_window_copy  # noqa: E402


@pytest.fixture
def repo_bundles(client):
    """Serve the REPO tiers bundles, not whatever data/remote-config holds
    on this machine: that dir is a persistent overlay (the same trap prod
    has) and a developer's stale local copy must not decide a test."""
    import json
    from pathlib import Path
    root = Path(__file__).parent.parent / "config" / "remote"
    rc = dict(client.app.state.remote_configs)
    for loc in ("tiers", "tiers.es", "tiers.fr", "tiers.ja"):
        rc[loc] = json.loads((root / f"{loc}.json").read_text())
    client.app.state.remote_configs = rc
    return client


def test_served_tiers_carry_the_number_and_no_raw_placeholder(repo_bundles):
    client = repo_bundles
    for path in ("/v1/tiers", "/v1/config/tiers"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        text = resp.text
        assert PLACEHOLDER not in text, f"{path} served a raw placeholder"
        assert "last 30 days" in text, f"{path} does not say the Plus window"


def test_localized_tiers_bundles_are_templated_too(repo_bundles):
    client = repo_bundles
    for lang, needle in (("es", "últimos 30 días"), ("fr", "30 derniers jours"), ("ja", "直近30日間")):
        resp = client.get("/v1/config/tiers", headers={"Accept-Language": lang})
        assert resp.status_code == 200
        assert PLACEHOLDER not in resp.text, lang
        assert needle in resp.text, lang


def test_render_is_pure_and_reads_the_dial_not_a_constant():
    rc = {"tiers": {"tiers": {"plus": {"feature_definitions": {"context_quilt": {"recall_max_age_days": 45}}}},
                    "copy": "your last {recall_window_days} days"}}
    out = render_recall_window_copy(rc["tiers"], rc)
    assert out["copy"] == "your last 45 days"
    assert rc["tiers"]["copy"] == "your last {recall_window_days} days", "served bundle was mutated"


def test_unset_dial_never_ships_a_brace_or_an_invented_number():
    rc = {"tiers": {"tiers": {"plus": {"feature_definitions": {}}}, "copy": "your last {recall_window_days} days"}}
    out = render_recall_window_copy(rc["tiers"], rc)
    assert "{" not in out["copy"] and "30" not in out["copy"]


def test_every_locale_carries_the_same_placeholder_count_and_a_plus_dial():
    """A locale missing a templated string would silently show old copy
    with no number; a locale missing the dial would render 'recent'."""
    import json
    from pathlib import Path
    root = Path(__file__).parent.parent / "config" / "remote"
    counts = {}
    for loc in ("tiers", "tiers.es", "tiers.fr", "tiers.ja"):
        d = json.loads((root / f"{loc}.json").read_text())
        counts[loc] = json.dumps(d, ensure_ascii=False).count(PLACEHOLDER)
        dial = d["tiers"]["plus"]["feature_definitions"]["context_quilt"]["recall_max_age_days"]
        assert isinstance(dial, int) and dial >= 1, loc
        assert d["tiers"]["pro"]["feature_definitions"]["context_quilt"]["recall_max_age_days"] is None, loc
    assert len(set(counts.values())) == 1, counts
    assert counts["tiers"] >= 6, counts


# --- The dossier leg (CQ #297, second commit) ------------------------------

from unittest.mock import MagicMock  # noqa: E402


@pytest.fixture
def cq_get():
    """Capture GET /v1/quilt/{user} params on the dossier fetch."""
    get = AsyncMock()
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {"meetings": [], "total_available": 0}
    resp.raise_for_status = lambda: None
    get.return_value = resp
    http_client = type("C", (), {"get": get, "post": AsyncMock()})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        yield get


def _dossier_params(get) -> dict:
    calls = [c for c in get.call_args_list if c.args and c.args[0].startswith("/v1/quilt/")]
    assert calls, "no GET /v1/quilt reached CQ"
    return calls[-1].kwargs["params"]


@pytest.mark.asyncio
async def test_dossier_fetch_carries_the_window_for_plus(cq_get):
    await cq.quilt_dossier("u-1", "p-1", app_id="shouldersurf", max_age_days=30)
    p = _dossier_params(cq_get)
    assert p["max_age_days"] == 30 and p["project_id"] == "p-1"


@pytest.mark.asyncio
async def test_dossier_fetch_sends_no_key_for_unlimited_and_never_zero(cq_get):
    for unlimited in (None, 0, -5, False):
        await cq.quilt_dossier("u-1", "p-1", app_id="shouldersurf", max_age_days=unlimited)
        assert "max_age_days" not in _dossier_params(cq_get), unlimited
