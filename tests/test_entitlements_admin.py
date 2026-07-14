"""Admin GET /webhooks/admin/entitlements — entitlements Phase 1
(docs/design/feature-entitlements.md §3): read-only aggregation of the
features × tiers matrix, the config-shaped knobs outside it, and
bundle-vs-overlay provenance.

Pins the contract:
- Bad admin key: 403; unknown app: 404
- Matrix mirrors tiers.yml feature_state exactly (bit-identical — this
  view is the Phase 2 migration-verification surface)
- Knobs resolve through the same loaders enforcement uses (documents,
  generation, project chat chars, search caps, max images)
- Per-app: techrehearsal applies apps.yml tier_overrides and echoes app
  identity; response carries the app registry for the dashboard selector
- Provenance names the slug + version per config and lists drift pointers
"""

from __future__ import annotations


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
    assert set(prov) == {"client-config", "tiers"}
    for entry in prov.values():
        assert entry["slug"] is None or isinstance(entry["slug"], str)
        assert isinstance(entry["drifted_pointers"], list)
    # SS (default app) resolves the flat slugs today
    assert prov["client-config"]["slug"] == "client-config"
    assert prov["client-config"]["version"] is not None
