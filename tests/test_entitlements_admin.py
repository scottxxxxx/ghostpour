"""Admin /webhooks/admin/entitlements — entitlements Phase 1 + 1.5
(docs/design/feature-entitlements.md §3): aggregation of the features ×
tiers matrix, the config-shaped knobs outside it, bundle-vs-overlay
provenance, and the targeted documents-knob editor.

Pins the contract:
- Bad admin key: 403; unknown app: 404
- Matrix mirrors tiers.yml feature_state exactly (bit-identical — this
  view is the Phase 2 migration-verification surface)
- Knobs resolve through the same loaders enforcement uses (documents,
  generation, project chat chars, search caps, max images) and carry
  derived per-tier availability computed with the gate's rank logic
- Per-app: techrehearsal applies apps.yml tier_overrides and echoes app
  identity; response carries the app registry for the dashboard selector
- Provenance names the slug + version per config and lists drift pointers
- PUT documents editor (Phase 1.5): closed enums, lockstep locale writes
  with version bumps, hot-reload so enforcement flips on the same request
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DOC_LOCALE_FILES = ["client-config.json", "client-config.es.json",
                     "client-config.ja.json"]
# every persistent file any test in this module writes (snapshot set)
_LOCALE_FILES = _DOC_LOCALE_FILES + ["entitlements.json"]


@pytest.fixture(autouse=True)
def _restore_persistent_files():
    """Snapshot the overlay files the PUT endpoint touches, restore after
    every test — even on failure (same pattern as the project-chat-cap
    tests; CONFIG_DIR is a module-level constant shared with the app)."""
    from app.routers.config import CONFIG_DIR
    snapshots: dict[str, str | None] = {}
    for fname in _LOCALE_FILES:
        path = CONFIG_DIR / fname
        snapshots[fname] = path.read_text() if path.exists() else None
    yield
    for fname, content in snapshots.items():
        path = CONFIG_DIR / fname
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.write_text(content)


def _get(client, params=None, admin_key="test-admin-key"):
    return client.get(
        "/webhooks/admin/entitlements",
        params=params or {},
        headers={"X-Admin-Key": admin_key},
    )


def test_bad_admin_key_returns_403(client):
    assert _get(client, admin_key="wrong").status_code == 403


def test_unknown_app_returns_404(client):
    assert _get(client, params={"app": "nope"}).status_code == 404


def test_matrix_mirrors_tier_config_exactly(client):
    resp = _get(client)
    assert resp.status_code == 200
    data = resp.json()
    tier_config = client.app.state.tier_config
    assert data["tiers"] == list(tier_config.tiers)
    for fname, f in data["matrix"].items():
        for t in data["tiers"]:
            assert f["tiers"][t] == tier_config.tiers[t].feature_state(fname)
            assert f["tiers"][t] in ("enabled", "teaser", "disabled")
    # every feature a tier references and every defined feature is a row
    feature_config = client.app.state.feature_config
    referenced = {f for t in tier_config.tiers.values() for f in t.features}
    assert set(data["matrix"]) == set(feature_config.features) | referenced
    # definitions ride along for the dashboard
    pc = data["matrix"].get("project_chat")
    assert pc and pc["display_name"] and pc["description"]


def test_knobs_resolve_through_enforcement_loaders(client):
    from app.services.client_config import project_chat_max_input_chars
    from app.services.document_generation import load_generation_config
    from app.services.documents import load_documents_config

    resp = _get(client)
    data = resp.json()
    k = data["knobs"]
    remote_configs = client.app.state.remote_configs

    docs = load_documents_config(remote_configs)
    gen = load_generation_config(remote_configs)
    assert k["documents"]["min_tier"] == docs["min_tier"]
    assert k["documents"]["max_files"] == docs["max_files"]
    assert "allowed_users" in k["documents"]
    assert "generation" not in k["documents"]  # split into its own knob
    assert k["document_generation"]["enabled"] == gen["enabled"]
    assert k["document_generation"]["min_tier"] == gen["min_tier"]
    for t in data["tiers"]:
        assert k["project_chat_max_input_chars"][t] == \
            project_chat_max_input_chars(remote_configs, t)
        assert set(k["search"][t]) == {
            "searches_per_month", "searches_soft_threshold"}
        assert isinstance(k["max_images_per_request"][t], int)


def test_techrehearsal_applies_tier_overrides(client):
    resp = _get(client, params={"app": "techrehearsal"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"]["id"] == "techrehearsal"
    assert data["app"]["dir"] == "techrehearsal"
    # apps.yml: TR caps every tier at 1 image per request
    assert set(data["knobs"]["max_images_per_request"].values()) == {1}
    assert data["provenance"]["tier_overrides"] == {
        "max_images_per_request": 1}
    # dashboard selector payload lists the registry
    assert {a["id"] for a in data["apps"]} >= {"shouldersurf", "techrehearsal"}


def test_provenance_names_slugs_and_drift_shape(client):
    data = _get(client).json()
    prov = data["provenance"]["configs"]
    assert set(prov) == {"entitlements", "client-config", "tiers"}
    for entry in prov.values():
        assert entry["slug"] is None or isinstance(entry["slug"], str)
        assert isinstance(entry["drifted_pointers"], list)
    # SS (default app) resolves the flat slugs today
    assert prov["client-config"]["slug"] == "client-config"
    assert prov["client-config"]["version"] is not None
    # Phase 2: the matrix's own config is a provenance row
    assert prov["entitlements"]["slug"] == "entitlements"


# --- Phase 2: the matrix is the single source ---

def test_bundle_matrix_bit_identical_to_tiers_yml():
    """The migration invariant (feature-entitlements.md §5.3): the seeded
    matrix resolves every cell exactly as the tiers.yml assignments it
    replaces. This test proves it BEFORE the YAML blocks are deleted."""
    import yaml

    bundle = json.load(open("config/remote/entitlements.json"))["matrix"]
    tiers = yaml.safe_load(open("config/tiers.yml"))["tiers"]
    features = {f for t in tiers.values() for f in (t.get("features") or {})}
    assert set(bundle) == features
    for fname in features:
        for tname, tdef in tiers.items():
            expected = (tdef.get("features") or {}).get(fname, "disabled")
            assert bundle[fname][tname] == expected, (fname, tname)


def test_resolver_defaults_and_validation():
    from app.services.entitlements import (
        completeness_warnings,
        entitlement_state,
        validate_matrix,
    )

    rc = {"entitlements": {"version": 1, "matrix": {
        "project_chat": {"free": "teaser", "pro": "enabled"}}}}
    assert entitlement_state(rc, "free", "project_chat") == "teaser"
    assert entitlement_state(rc, "pro", "project_chat") == "enabled"
    assert entitlement_state(rc, "plus", "project_chat") == "disabled"  # missing cell
    assert entitlement_state(rc, "pro", "nope") == "disabled"           # missing feature
    assert entitlement_state({}, "pro", "project_chat") == "disabled"   # no config

    known_f, known_t = {"project_chat"}, {"free", "plus", "pro"}
    assert validate_matrix({"matrix": {"project_chat": {"free": "teaser"}}},
                           known_features=known_f, known_tiers=known_t) == []
    bad = validate_matrix(
        {"matrix": {"ghost": {"free": "enabled"},
                    "project_chat": {"gold": "enabled", "free": "on"}}},
        known_features=known_f, known_tiers=known_t)
    assert any("unknown feature 'ghost'" in p for p in bad)
    assert any("unknown tier 'gold'" in p for p in bad)
    assert any("invalid state 'on'" in p for p in bad)
    assert validate_matrix({}, known_features=known_f, known_tiers=known_t)

    warns = completeness_warnings(rc, known_features={"project_chat"},
                                  known_tiers=known_t)
    assert warns == ["project_chat.plus"]


def test_matrix_cell_put_flips_enforcement(client, free_user):
    from app.routers.config import CONFIG_DIR
    from app.services.entitlements import entitlement_state

    def _cell(body, key="test-admin-key"):
        return client.put("/webhooks/admin/entitlements/matrix", json=body,
                          headers={"X-Admin-Key": key})

    assert _cell({"feature": "web_search", "tier": "free",
                  "state": "enabled"}, key="wrong").status_code == 403
    assert _cell({"feature": "ghost", "tier": "free",
                  "state": "enabled"}).status_code == 400
    assert _cell({"feature": "web_search", "tier": "gold",
                  "state": "enabled"}).status_code == 400
    assert _cell({"feature": "web_search", "tier": "free",
                  "state": "on"}).status_code == 400

    before = json.loads(
        (CONFIG_DIR / "entitlements.json").read_text())["version"]
    r = _cell({"feature": "web_search", "tier": "free", "state": "enabled"})
    assert r.status_code == 200 and r.json()["status"] == "updated"
    assert r.json()["version"] == before + 1
    # hot-reload: the resolver enforcement reads sees it immediately
    assert entitlement_state(client.app.state.remote_configs,
                             "free", "web_search") == "enabled"
    # the GET view reflects it
    data = _get(client).json()
    assert data["matrix"]["web_search"]["tiers"]["free"] == "enabled"
    # a signed-in free user's served features map reflects it
    me = client.get("/v1/usage/me", headers=free_user["headers"]).json()
    assert me["features"]["web_search"] == "enabled"
    # idempotent resend
    assert _cell({"feature": "web_search", "tier": "free",
                  "state": "enabled"}).json()["status"] == "unchanged"


def test_config_put_validates_entitlements_matrix(client):
    good = {"version": 999,
            "matrix": {"web_search": {"free": "disabled", "plus": "enabled",
                                      "pro": "enabled", "admin": "enabled"}}}
    r = client.put("/webhooks/admin/config/entitlements", json={"data": good},
                   headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200
    bad = {"version": 1000, "matrix": {"ghost": {"free": "enabled"}}}
    r2 = client.put("/webhooks/admin/config/entitlements", json={"data": bad},
                    headers={"X-Admin-Key": "test-admin-key"})
    assert r2.status_code == 400
    assert "unknown feature 'ghost'" in str(r2.json())


# --- Phase 1.5: derived availability + the documents-knob editor ---

def _put(client, body, admin_key="test-admin-key"):
    return client.put(
        "/webhooks/admin/entitlements/documents",
        json=body,
        headers={"X-Admin-Key": admin_key},
    )


def test_tier_availability_derives_from_min_tier(client):
    data = _get(client).json()
    gen = data["knobs"]["document_generation"]
    avail = gen["tier_availability"]
    assert set(avail) == set(data["tiers"])
    if gen["enabled"] and gen["min_tier"] == "pro":
        # admin reads disabled: unranked in the gate's _TIER_RANK today
        assert avail == {"free": False, "plus": False,
                         "pro": True, "admin": False}
    if not gen["enabled"]:
        assert not any(avail.values())


def test_put_rejects_bad_inputs(client):
    assert _put(client, {"scope": "generation", "enabled": True},
                admin_key="wrong").status_code == 403
    assert _put(client, {"scope": "nope", "enabled": True}).status_code == 400
    assert _put(client, {"scope": "generation"}).status_code == 400
    assert _put(client, {"scope": "generation",
                         "min_tier": "admin"}).status_code == 400
    assert _put(client, {"scope": "generation",
                         "min_tier": "platinum"}).status_code == 400


def test_put_generation_min_tier_writes_lockstep_and_hot_reloads(client):
    from app.routers.config import CONFIG_DIR
    from app.services.document_generation import load_generation_config

    before = {f: json.loads((CONFIG_DIR / f).read_text()).get("version")
              for f in _DOC_LOCALE_FILES if (CONFIG_DIR / f).exists()}
    r = _put(client, {"scope": "generation", "min_tier": "plus"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "updated"
    updated = {f["slug"]: f for f in d["files_updated"]}
    for fname in before:
        slug = fname.removesuffix(".json")
        # every locale file present on disk changed, version bumped by 1
        assert updated[slug]["version"] == (before[fname] or 0) + 1
        assert updated[slug]["min_tier"]["new"] == "plus"
        on_disk = json.loads((CONFIG_DIR / fname).read_text())
        assert on_disk["documents"]["generation"]["min_tier"] == "plus"
    # hot-reload: enforcement's loader sees the new value immediately
    assert load_generation_config(
        client.app.state.remote_configs)["min_tier"] == "plus"
    # and the GET reflects it, including derived availability
    data = _get(client).json()
    gen = data["knobs"]["document_generation"]
    assert gen["min_tier"] == "plus"
    if gen["enabled"]:
        assert gen["tier_availability"]["plus"] is True
    # idempotent resend reports unchanged
    assert _put(client, {"scope": "generation",
                         "min_tier": "plus"}).json()["status"] == "unchanged"


def test_put_passthrough_enabled_toggles_documents_gate(client):
    from app.services.documents import load_documents_config

    r = _put(client, {"scope": "passthrough", "enabled": False})
    assert r.status_code == 200
    assert load_documents_config(
        client.app.state.remote_configs)["enabled"] is False
    data = _get(client).json()
    assert data["knobs"]["documents"]["enabled"] is False
    assert not any(data["knobs"]["documents"]["tier_availability"].values())
    # generation block untouched by a passthrough-scope write
    for f in _DOC_LOCALE_FILES:
        from app.routers.config import CONFIG_DIR
        if (CONFIG_DIR / f).exists():
            gen = json.loads(
                (CONFIG_DIR / f).read_text())["documents"].get("generation")
            assert gen and "enabled" in gen
