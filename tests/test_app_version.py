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


_CHANNEL_REGISTRY = {
    "com.example.app": {
        "platforms": {
            "ios": {
                "latest": {"version": "1.0", "build": "1",
                           "upgrade_url": "https://fallback.test"},
                "latest_by_channel": {
                    "testflight": {"version": "1.14", "build": "749",
                                   "upgrade_url": "https://testflight.test"},
                    "appstore": {"version": "0.0", "build": "0",
                                 "upgrade_url": ""},
                },
            },
        },
    },
}


def test_channel_selects_testflight_latest():
    info = get_version_info(_CHANNEL_REGISTRY, "com.example.app", "testflight")
    ios = info["platforms"]["ios"]
    assert ios["latest"]["version"] == "1.14"
    assert ios["latest_version"] == "1.14"  # flat alias too
    assert ios["upgrade_url"] == "https://testflight.test"


def test_channel_selects_appstore_latest():
    info = get_version_info(_CHANNEL_REGISTRY, "com.example.app", "appstore")
    ios = info["platforms"]["ios"]
    assert ios["latest"]["version"] == "0.0"  # inert until App Store launch


def test_no_channel_falls_back_to_latest():
    info = get_version_info(_CHANNEL_REGISTRY, "com.example.app", None)
    assert info["platforms"]["ios"]["latest"]["version"] == "1.0"


def test_unknown_channel_falls_back_to_latest():
    info = get_version_info(_CHANNEL_REGISTRY, "com.example.app", "nonsense")
    assert info["platforms"]["ios"]["latest"]["version"] == "1.0"


def test_latest_by_channel_never_leaks_to_wire():
    for ch in (None, "testflight", "appstore"):
        info = get_version_info(_CHANNEL_REGISTRY, "com.example.app", ch)
        assert "latest_by_channel" not in info["platforms"]["ios"]


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
    # Nested shape (canonical, what future clients read).
    assert ios["latest"]["version"] == "1.13"
    assert ios["latest"]["upgrade_url"].startswith("https://")
    # Flat aliases (additive, what 1.13's flat decoder reads).
    assert ios["latest_version"] == "1.13"
    assert ios["upgrade_url"].startswith("https://")
    assert ios["min_supported_version"] == "1.0"
    assert resp.headers["cache-control"].startswith("public")


def test_response_includes_flat_aliases_for_1_13_decoder(client_with_versions):
    """SS's 1.13 build (build 377, shipped 2026-06-03) decodes
    `latest_version` + `upgrade_url` as flat fields on the platform.
    Without the additive aliases, every 1.13 device silently reports
    'no update available' even when a newer version ships, because
    its decoder sees null for the field it expects. Pin the contract."""
    resp = client_with_versions.get(
        "/v1/app/version",
        headers={"X-App-Bundle-Id": "com.shouldersurf.ShoulderSurf"},
    )
    ios = resp.json()["platforms"]["ios"]
    # Flat siblings must mirror the nested values exactly.
    assert ios["latest_version"] == ios["latest"]["version"]
    assert ios["upgrade_url"] == ios["latest"]["upgrade_url"]


def test_get_version_info_emits_flat_aliases():
    """Unit test the helper directly: nested platforms input → flat
    aliases on the way out."""
    registry = {
        "com.example.app": {
            "platforms": {
                "ios": {
                    "latest": {
                        "version": "2.0",
                        "upgrade_url": "https://example.com/u",
                    },
                    "min_supported_version": "1.0",
                },
            },
        },
    }
    info = get_version_info(registry, "com.example.app")
    ios = info["platforms"]["ios"]
    assert ios["latest"]["version"] == "2.0"
    assert ios["latest_version"] == "2.0"
    assert ios["upgrade_url"] == "https://example.com/u"


def test_aliases_do_not_overwrite_existing_flat_fields():
    """Defensive: if an operator one day puts flat fields in the YAML
    directly, don't clobber them with the nested values."""
    registry = {
        "com.example.app": {
            "platforms": {
                "ios": {
                    "latest": {"version": "2.0", "upgrade_url": "https://A", "build": "999"},
                    "latest_version": "1.99",
                    "upgrade_url": "https://B",
                    "latest_build": "888",
                    "min_supported_version": "1.0",
                },
            },
        },
    }
    info = get_version_info(registry, "com.example.app")
    ios = info["platforms"]["ios"]
    assert ios["latest_version"] == "1.99"  # explicit value preserved
    assert ios["upgrade_url"] == "https://B"
    assert ios["latest_build"] == "888"


def test_get_version_info_emits_latest_build_flat_alias():
    """SS adds `build` under latest; we mirror it as flat `latest_build`."""
    registry = {
        "com.example.app": {
            "platforms": {
                "ios": {
                    "latest": {
                        "version": "1.13",
                        "build": "447",
                        "upgrade_url": "https://x.test",
                    },
                    "min_supported_version": "1.0",
                },
            },
        },
    }
    info = get_version_info(registry, "com.example.app")
    ios = info["platforms"]["ios"]
    assert ios["latest"]["build"] == "447"
    assert ios["latest_build"] == "447"


def test_get_version_info_omits_build_when_absent():
    """Backward compat: registry without a build field omits the alias
    entirely. Every iOS build in the field before 451 ignores the
    field anyway, so a missing alias is semantically identical to a
    present one with no consumers."""
    registry = {
        "com.example.app": {
            "platforms": {
                "ios": {
                    "latest": {"version": "1.13", "upgrade_url": "https://x.test"},
                    "min_supported_version": "1.0",
                },
            },
        },
    }
    info = get_version_info(registry, "com.example.app")
    ios = info["platforms"]["ios"]
    assert "build" not in ios["latest"]
    assert "latest_build" not in ios


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


@pytest.fixture
def client_with_channels(client):
    """Registry with per-channel latest, to exercise the X-App-Distribution
    header mapping end to end through the endpoint."""
    from app.main import app
    registry = {
        "com.shouldersurf.ShoulderSurf": {
            "platforms": {
                "ios": {
                    "latest": {"version": "1.0", "build": "1",
                               "upgrade_url": "https://fallback.test"},
                    "latest_by_channel": {
                        "testflight": {"version": "1.14", "build": "749",
                                       "upgrade_url": "https://testflight.test"},
                        "appstore": {"version": "0.0", "build": "0",
                                     "upgrade_url": ""},
                    },
                },
            },
        },
    }
    prior = getattr(app.state, "app_versions", None)
    app.state.app_versions = registry
    yield client
    if prior is not None:
        app.state.app_versions = prior


def _latest_for(client_with_channels, distribution):
    headers = {"X-App-Bundle-Id": "com.shouldersurf.ShoulderSurf"}
    if distribution is not None:
        headers["X-App-Distribution"] = distribution
    resp = client_with_channels.get("/v1/app/version", headers=headers)
    assert resp.status_code == 200
    return resp.json()["platforms"]["ios"]["latest"]


def test_endpoint_production_maps_to_appstore(client_with_channels):
    assert _latest_for(client_with_channels, "production")["version"] == "0.0"


def test_endpoint_sandbox_maps_to_testflight(client_with_channels):
    latest = _latest_for(client_with_channels, "sandbox")
    assert latest["version"] == "1.14"
    assert latest["upgrade_url"] == "https://testflight.test"


def test_endpoint_xcode_maps_to_testflight(client_with_channels):
    assert _latest_for(client_with_channels, "xcode")["version"] == "1.14"


def test_endpoint_no_distribution_header_falls_back(client_with_channels):
    assert _latest_for(client_with_channels, None)["version"] == "1.0"


def test_endpoint_sets_vary_on_distribution(client_with_channels):
    resp = client_with_channels.get(
        "/v1/app/version",
        headers={"X-App-Bundle-Id": "com.shouldersurf.ShoulderSurf",
                 "X-App-Distribution": "production"},
    )
    assert "X-App-Distribution" in resp.headers.get("Vary", "")
