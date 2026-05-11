"""Tests for the bundle-vs-persistent sync admin endpoints.

The footgun this closes: `seed_remote_configs()` only seeds files
that don't exist in the persistent dir. After first deploy, every
bundle change to an existing slug is silently ignored — dashboard
edits are preserved (good) but legitimate bundle improvements never
reach prod (bad). This endpoint lets an admin opt-in to syncing
specific top-level keys from bundle → persistent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_KEY = {"X-Admin-Key": "test-admin-key"}


def _persistent_dir() -> Path:
    from app.routers.config import CONFIG_DIR
    return CONFIG_DIR


def _bundle_dir() -> Path:
    from app.routers.config import _BUNDLED_DIR
    return _BUNDLED_DIR


@pytest.fixture(autouse=True)
def _isolate_test_slug():
    """Use a dedicated test slug ('sync-test') so we don't touch any
    real config file in either bundle or persistent. Fixture creates
    a fresh bundle file for the test and removes both copies after."""
    bundle_path = _bundle_dir() / "sync-test.json"
    persistent_path = _persistent_dir() / "sync-test.json"

    bundle_path.write_text(json.dumps({
        "version": 5,
        "alpha": "from_bundle",
        "beta": 42,
        "nested": {"a": 1, "b": 2},
    }, indent=2))

    yield

    if bundle_path.exists():
        bundle_path.unlink()
    if persistent_path.exists():
        persistent_path.unlink()


# ---------------------------------------------------------------------------
# GET /admin/config/{slug}/bundle
# ---------------------------------------------------------------------------

def test_get_bundle_returns_bundled_content(client):
    resp = client.get("/webhooks/admin/config/sync-test/bundle", headers=_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "sync-test"
    assert body["data"]["alpha"] == "from_bundle"
    assert body["data"]["beta"] == 42


def test_get_bundle_404_for_unknown_slug(client):
    resp = client.get("/webhooks/admin/config/no-such-slug/bundle", headers=_KEY)
    assert resp.status_code == 404


def test_get_bundle_requires_admin_key(client):
    resp = client.get("/webhooks/admin/config/sync-test/bundle")
    assert resp.status_code in (401, 422)


# ---------------------------------------------------------------------------
# POST /admin/config/{slug}/sync-from-bundle
# ---------------------------------------------------------------------------

def test_sync_creates_persistent_when_missing(client):
    """The `client` fixture's lifespan auto-seeds the persistent file
    from bundle. Delete it explicitly to simulate the "missing
    persistent" case the endpoint should handle."""
    persistent_path = _persistent_dir() / "sync-test.json"
    if persistent_path.exists():
        persistent_path.unlink()

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["alpha", "beta"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "synced"
    assert body["version"] == 1  # bootstrapped from 0 → 1

    persisted = json.loads(persistent_path.read_text())
    assert persisted["alpha"] == "from_bundle"
    assert persisted["beta"] == 42


def test_sync_preserves_unrequested_persistent_keys(client):
    """The whole point: dashboard edits to OTHER keys must be preserved."""
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 3,
        "alpha": "dashboard_edited",
        "beta": 999,
        "dashboard_only": "important_value",
    }))

    # Sync only alpha
    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["alpha"]},
    )
    assert resp.status_code == 200

    persisted = json.loads(persistent_path.read_text())
    # alpha got synced from bundle
    assert persisted["alpha"] == "from_bundle"
    # beta and dashboard_only stayed at the persistent values
    assert persisted["beta"] == 999
    assert persisted["dashboard_only"] == "important_value"
    # version bumped
    assert persisted["version"] == 4


def test_sync_reports_per_key_changes(client):
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 1,
        "alpha": "from_bundle",  # already matches
        "beta": 7,                # differs from bundle's 42
    }))

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["alpha", "beta"]},
    )
    body = resp.json()
    by_key = {c["key"]: c for c in body["changes"]}
    assert by_key["alpha"]["status"] == "unchanged"
    assert by_key["beta"]["status"] == "synced"
    assert by_key["beta"]["old"] == 7
    assert by_key["beta"]["new"] == 42


def test_sync_with_no_diff_does_not_bump_version(client):
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 5,
        "alpha": "from_bundle",
        "beta": 42,
    }))

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["alpha", "beta"]},
    )
    body = resp.json()
    assert body["status"] == "no_changes"
    assert body["version"] == 5  # NOT bumped


def test_sync_top_level_block_replaces_whole_value(client):
    """Bare top-level key still replaces the whole value verbatim
    (legacy semantics). Use a JSON pointer for sub-key surgery."""
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 1,
        "nested": {"a": "old", "extra": "gone-because-whole-block-synced"},
    }))

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["nested"]},
    )
    assert resp.status_code == 200
    persisted = json.loads(persistent_path.read_text())
    assert persisted["nested"] == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# JSON-pointer (nested-path) syncing — the gap that motivated the endpoint.
# ---------------------------------------------------------------------------


def _write_nested_bundle() -> None:
    """Overwrite the test slug's bundle with a nested shape that mirrors
    the PR #121 client-config scenario (limits.project_chat.*)."""
    bundle_path = _bundle_dir() / "sync-test.json"
    bundle_path.write_text(json.dumps({
        "version": 9,
        "limits": {
            "project_chat": {
                "max_input_chars": {"free": 200000, "plus": 600000, "pro": 720000},
                "defaultPromptReserveTokens": 4096,
            },
        },
        "flags": {"experimental": True},
    }, indent=2))


def test_sync_nested_pointer_replaces_only_the_leaf(client):
    """JSON pointer surgery: a deep bundle add lands without touching
    sibling dashboard edits in the same parent dict."""
    _write_nested_bundle()
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 7,
        "limits": {
            "project_chat": {
                "max_input_chars": {"free": 999, "plus": 888, "pro": 777},
                # defaultPromptReserveTokens absent — this is the bundle add
            },
        },
        "flags": {"experimental": False, "dashboard_only": "kept"},
    }))

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["/limits/project_chat/defaultPromptReserveTokens"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "synced"

    persisted = json.loads(persistent_path.read_text())
    pc = persisted["limits"]["project_chat"]
    assert pc["defaultPromptReserveTokens"] == 4096      # new leaf landed
    assert pc["max_input_chars"] == {                     # siblings untouched
        "free": 999, "plus": 888, "pro": 777,
    }
    assert persisted["flags"] == {                        # other top-level keys untouched
        "experimental": False, "dashboard_only": "kept",
    }


def test_sync_nested_pointer_creates_intermediate_dicts(client):
    """Persistent missing an intermediate path — endpoint backfills it."""
    _write_nested_bundle()
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({"version": 1}))  # nothing else

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["/limits/project_chat/defaultPromptReserveTokens"]},
    )
    assert resp.status_code == 200
    persisted = json.loads(persistent_path.read_text())
    assert persisted["limits"]["project_chat"]["defaultPromptReserveTokens"] == 4096


def test_sync_400_when_nested_pointer_not_in_bundle(client):
    _write_nested_bundle()
    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["/limits/project_chat/no_such_field"]},
    )
    assert resp.status_code == 400
    assert "/limits/project_chat/no_such_field" in resp.text


def test_sync_nested_pointer_reports_old_and_new(client):
    """Per-key change report includes both pre and post values."""
    _write_nested_bundle()
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 1,
        "limits": {"project_chat": {"defaultPromptReserveTokens": 1024}},
    }))

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["/limits/project_chat/defaultPromptReserveTokens"]},
    )
    body = resp.json()
    change = next(
        c for c in body["changes"]
        if c["key"] == "/limits/project_chat/defaultPromptReserveTokens"
    )
    assert change["status"] == "synced"
    assert change["old"] == 1024
    assert change["new"] == 4096


def test_sync_mixed_top_level_and_nested_in_one_call(client):
    """Same request can mix legacy top-level keys and JSON pointers."""
    _write_nested_bundle()
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 1,
        "limits": {"project_chat": {"defaultPromptReserveTokens": 1024}},
        "flags": {"experimental": False},
    }))

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": [
            "flags",  # top-level whole-block
            "/limits/project_chat/defaultPromptReserveTokens",  # nested leaf
        ]},
    )
    assert resp.status_code == 200
    persisted = json.loads(persistent_path.read_text())
    assert persisted["flags"] == {"experimental": True}                       # bundle won
    assert persisted["limits"]["project_chat"]["defaultPromptReserveTokens"] == 4096


def test_sync_400_when_key_not_in_bundle(client):
    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["alpha", "no_such_key"]},
    )
    assert resp.status_code == 400
    assert "no_such_key" in resp.text


def test_sync_400_on_empty_keys_list(client):
    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": []},
    )
    assert resp.status_code == 400


def test_sync_404_for_unknown_slug(client):
    resp = client.post(
        "/webhooks/admin/config/no-such-slug/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["x"]},
    )
    assert resp.status_code == 404


def test_sync_requires_admin_key(client):
    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        json={"keys": ["alpha"]},
    )
    assert resp.status_code in (401, 422)


def test_sync_hot_reloads_remote_configs(client):
    """After sync, /v1/config/{slug} should serve the new value
    immediately — no container restart required."""
    # Seed persistent with a different value
    persistent_path = _persistent_dir() / "sync-test.json"
    persistent_path.write_text(json.dumps({
        "version": 1,
        "alpha": "stale",
    }))
    # Trigger remote_configs reload by hitting any admin GET
    client.get("/webhooks/admin/config/sync-test", headers=_KEY)

    resp = client.post(
        "/webhooks/admin/config/sync-test/sync-from-bundle",
        headers=_KEY,
        json={"keys": ["alpha"]},
    )
    assert resp.status_code == 200

    # Public /v1/config/{slug} should now serve the synced value
    pub = client.get("/v1/config/sync-test", headers={"X-Config-Version": "0"})
    assert pub.status_code == 200
    assert pub.json()["alpha"] == "from_bundle"
