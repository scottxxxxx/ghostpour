"""Per-app version endpoint + registry loader tests.

Pins the multi-tenant contract:
- missing X-App-Bundle-Id is 400, not 404 (request shape problem)
- unknown bundle id is 404 (this gateway doesn't know that app)
- known bundle id returns the platforms block in the wire shape SS is reading
- no auth required (call fires pre-login on launch)
"""

from __future__ import annotations

import textwrap

import pytest

from app.services.app_version import get_version_info, load_registry


# --- Loader unit tests -----------------------------------------------------


def test_load_registry_missing_file_returns_empty(tmp_path):
    r = load_registry(tmp_path / "does-not-exist.yml")
    assert r == {}


def test_load_registry_malformed_yaml_returns_empty(tmp_path):
    p = tmp_path / "broken.yml"
    p.write_text("not: valid: yaml: at all: [")
    r = load_registry(p)
    assert r == {}


def test_load_registry_non_mapping_root_returns_empty(tmp_path):
    p = tmp_path / "list.yml"
    p.write_text("- one\n- two\n")
    r = load_registry(p)
    assert r == {}


def test_load_registry_parses_real_shape(tmp_path):
    p = tmp_path / "versions.yml"
    p.write_text(textwrap.dedent("""\
        com.example.app:
          platforms:
            ios:
              latest:
                version: "1.2"
                upgrade_url: "https://example.com/upgrade"
              min_supported_version: "1.0"
    """))
    r = load_registry(p)
    assert "com.example.app" in r
    ios = r["com.example.app"]["platforms"]["ios"]
    assert ios["latest"]["version"] == "1.2"
    assert ios["latest"]["upgrade_url"] == "https://example.com/upgrade"
    assert ios["min_supported_version"] == "1.0"


def test_get_version_info_hit():
    registry = {
        "com.example.app": {
            "platforms": {
                "ios": {
                    "latest": {"version": "1.2", "upgrade_url": "https://x.test"},
                    "min_supported_version": "1.0",
                },
            },
        },
    }
    info = get_version_info(registry, "com.example.app")
    assert info["bundle_id"] == "com.example.app"
    assert info["platforms"]["ios"]["latest"]["version"] == "1.2"
    assert info["platforms"]["ios"]["latest"]["upgrade_url"] == "https://x.test"


def test_get_version_info_miss():
    assert get_version_info({}, "anything") is None


def test_get_version_info_entry_with_no_platforms_block_is_miss():
    registry = {"com.example.app": {"notes": "we forgot platforms"}}
    assert get_version_info(registry, "com.example.app") is None


# --- Endpoint integration tests -------------------------------------------


@pytest.fixture
def client_with_versions(client, tmp_path, monkeypatch):
    """Inject a known registry onto the running app via app.state."""
    from app.main import app
    registry = {
        "com.shouldersurf.ShoulderSurf": {
            "platforms": {
                "ios": {
                    "latest": {
                        "version": "1.13",
                        "upgrade_url": "https://testflight.apple.com/join/ubRWVcXF",
                    },
                    "min_supported_version": "1.0",
                },
            },
        },
    }
    prior = getattr(app.state, "app_versions", None)
    app.state.app_versions = registry
    yield client
    if prior is not None:
        app.state.app_versions = prior


def test_missing_bundle_id_returns_400(client_with_versions):
    resp = client_with_versions.get("/v1/app/version")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "missing_bundle_id"


def test_unknown_bundle_id_returns_404(client_with_versions):
    resp = client_with_versions.get(
        "/v1/app/version",
        headers={"X-App-Bundle-Id": "com.nobody.unknown"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_bundle_id"


def test_known_bundle_id_returns_200_with_platforms(client_with_versions):
    resp = client_with_versions.get(
        "/v1/app/version",
        headers={"X-App-Bundle-Id": "com.shouldersurf.ShoulderSurf"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bundle_id"] == "com.shouldersurf.ShoulderSurf"
    ios = body["platforms"]["ios"]
    assert ios["latest"]["version"] == "1.13"
    assert ios["latest"]["upgrade_url"].startswith("https://")
    assert ios["min_supported_version"] == "1.0"
    assert resp.headers["cache-control"].startswith("public")


def test_endpoint_requires_no_auth(client_with_versions):
    """No Authorization header should be needed — the call fires
    pre-login on launch. Pin that explicitly."""
    resp = client_with_versions.get(
        "/v1/app/version",
        headers={"X-App-Bundle-Id": "com.shouldersurf.ShoulderSurf"},
    )
    assert resp.status_code == 200


def test_empty_bundle_id_string_returns_400(client_with_versions):
    """Whitespace-only header counts as missing."""
    resp = client_with_versions.get(
        "/v1/app/version",
        headers={"X-App-Bundle-Id": "   "},
    )
    assert resp.status_code == 400
