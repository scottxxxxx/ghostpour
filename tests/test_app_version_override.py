"""Break-glass force-upgrade flip: overlay merge + the admin endpoint flipping
the gate live (no deploy), and standing it back down."""

import pytest

from app.services import app_version as av

ADMIN = {"X-Admin-Key": "test-admin-key"}
SS_BUNDLE = "com.shouldersurf.ShoulderSurf"
SS_HEADERS = {"X-App-ID": "shouldersurf", "X-App-Version": "1.4", "X-App-Build": "500"}


@pytest.fixture(autouse=True)
def _clean_overlay(client):
    """Each test starts and ends with an empty overlay + a fresh effective
    registry, so a flip in one test can't leak into another."""
    from app.config import get_settings
    av.save_overlay({})
    client.app.state.app_versions = av.load_effective(get_settings().app_versions_path)
    yield
    av.save_overlay({})
    client.app.state.app_versions = av.load_effective(get_settings().app_versions_path)


def test_merge_overlay_overrides_platform_keys_without_mutating_base():
    base = {SS_BUNDLE: {"platforms": {"ios": {"min_supported_version": "1.0", "min_supported_blocking": False}}}}
    over = {SS_BUNDLE: {"platforms": {"ios": {"min_supported_blocking": True, "min_supported_version": "1.5"}}}}
    eff = av.merge_overlay(base, over)
    ios = eff[SS_BUNDLE]["platforms"]["ios"]
    assert ios["min_supported_blocking"] is True
    assert ios["min_supported_version"] == "1.5"
    # base untouched
    assert base[SS_BUNDLE]["platforms"]["ios"]["min_supported_blocking"] is False


def test_overlay_roundtrip(client):
    av.save_overlay({SS_BUNDLE: {"platforms": {"ios": {"min_supported_blocking": True}}}})
    assert av.load_overlay()[SS_BUNDLE]["platforms"]["ios"]["min_supported_blocking"] is True


def test_override_endpoint_flips_gate_live_then_stands_down(client):
    # baseline: below-floor build is NOT blocked (flag off in the YAML)
    assert client.post("/v1/chat", json={}, headers=SS_HEADERS).status_code != 426

    # break glass: flip blocking on at runtime
    r = client.post("/webhooks/admin/app-version/override", headers=ADMIN, json={
        "bundle_id": SS_BUNDLE, "min_supported_version": "1.5", "min_supported_blocking": True})
    assert r.status_code == 200
    assert r.json()["effective"]["min_supported_blocking"] is True

    # same below-floor call is now 426'd, no deploy
    blocked = client.post("/v1/chat", json={}, headers=SS_HEADERS)
    assert blocked.status_code == 426
    assert blocked.json()["code"] == "upgrade_required"
    assert blocked.json()["min_supported_version"] == "1.5"

    # GET shows the live overlay
    state = client.get("/webhooks/admin/app-version", headers=ADMIN).json()
    assert state["overlay"][SS_BUNDLE]["platforms"]["ios"]["min_supported_blocking"] is True

    # stand down: clear the override -> serves again
    rc = client.delete(f"/webhooks/admin/app-version/override/{SS_BUNDLE}", headers=ADMIN)
    assert rc.status_code == 200 and rc.json()["status"] == "cleared"
    assert client.post("/v1/chat", json={}, headers=SS_HEADERS).status_code != 426


def test_override_blocklist_single_build(client):
    # cut off one build (X-App-Build) even with the flag off / above the floor
    client.post("/webhooks/admin/app-version/override", headers=ADMIN, json={
        "bundle_id": SS_BUNDLE, "blocked_versions": ["500"]})
    blocked = client.post("/v1/chat", json={}, headers={**SS_HEADERS, "X-App-Version": "9.9"})
    assert blocked.status_code == 426
    # a different build is fine
    assert client.post("/v1/chat", json={}, headers={**SS_HEADERS, "X-App-Build": "501", "X-App-Version": "9.9"}).status_code != 426


def test_override_requires_admin(client):
    assert client.post("/webhooks/admin/app-version/override",
                       headers={"X-Admin-Key": "wrong"},
                       json={"bundle_id": SS_BUNDLE, "min_supported_blocking": True}).status_code == 403
