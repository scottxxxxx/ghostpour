"""Tests for the periodic device-models sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import device_models, device_models_sync


# --- parser ---------------------------------------------------------------


def test_parse_gist_basic():
    text = """
    iPhone17,3 : iPhone 16
    iPhone17,2 : iPhone 16 Pro Max
    iPad16,8  :  iPad Air 11-inch (M4)
    """
    out = device_models_sync.parse_gist(text)
    assert out["iPhone17,3"] == "iPhone 16"
    assert out["iPhone17,2"] == "iPhone 16 Pro Max"
    assert out["iPad16,8"] == "iPad Air 11-inch (M4)"


def test_parse_gist_ignores_comments_and_blanks():
    text = """
    # This is a comment
    iPhone12,1 : iPhone 11

    iPhone12,3 : iPhone 11 Pro
    """
    out = device_models_sync.parse_gist(text)
    assert len(out) == 2


def test_parse_gist_keeps_only_known_prefixes():
    text = """
    iPhone17,3 : iPhone 16
    AppleTV6,1 : Apple TV 4K
    Watch6,1 : Apple Watch SE
    iPad16,8 : iPad Air 11
    arm64 : Simulator
    """
    out = device_models_sync.parse_gist(text)
    assert "iPhone17,3" in out
    assert "iPad16,8" in out
    assert "arm64" in out
    assert "AppleTV6,1" not in out
    assert "Watch6,1" not in out


def test_parse_gist_handles_no_colon_line():
    text = """
    iPhone17,3 : iPhone 16
    this line has no colon and should be skipped
    """
    out = device_models_sync.parse_gist(text)
    assert out == {"iPhone17,3": "iPhone 16"}


# --- lookup priority ------------------------------------------------------


def test_to_marketing_name_prefers_synced_map(monkeypatch):
    """When a synced entry exists, it wins over the static fallback."""
    monkeypatch.setattr(
        device_models_sync,
        "_synced",
        {"iPhone17,2": "iPhone 16 Pro Max (synced)"},
    )
    assert device_models.to_marketing_name("iPhone17,2") == "iPhone 16 Pro Max (synced)"


def test_to_marketing_name_falls_back_to_static_on_sync_miss(monkeypatch):
    """When the synced map doesn't have the code, the static table answers."""
    monkeypatch.setattr(device_models_sync, "_synced", {})
    # iPad16,8 was added to the static table in the same PR as this test.
    assert device_models.to_marketing_name("iPad16,8") == 'iPad Air 11" (M4)'


def test_to_marketing_name_unknown_returns_raw_in_brackets(monkeypatch):
    monkeypatch.setattr(device_models_sync, "_synced", {})
    name = device_models.to_marketing_name("iPhone99,99")
    assert name == "Unknown (iPhone99,99)"


# --- fetch_and_refresh ---------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_refresh_success_updates_module_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_text = "iPhone17,3 : iPhone 16\niPad16,8 : iPad Air 11-inch (M4)\n"

    async def _stub():
        return fake_text

    device_models_sync._synced = {}
    monkeypatch.setattr(device_models_sync, "_fetch_gist_text", _stub)
    count, detail = await device_models_sync.fetch_and_refresh()

    assert count == 2
    assert "refreshed 2" in detail
    assert device_models_sync._synced["iPhone17,3"] == "iPhone 16"
    assert (tmp_path / "data" / "device_models_synced.json").exists()


@pytest.mark.asyncio
async def test_fetch_and_refresh_network_failure_is_fail_soft(monkeypatch):
    async def _stub():
        raise httpx.HTTPError("network down")

    prior = dict(device_models_sync._synced)
    monkeypatch.setattr(device_models_sync, "_fetch_gist_text", _stub)
    count, detail = await device_models_sync.fetch_and_refresh()

    assert count == 0
    assert "fetch failed" in detail
    assert device_models_sync._synced == prior


@pytest.mark.asyncio
async def test_fetch_and_refresh_zero_entries_does_not_clobber_existing(monkeypatch):
    """If parsing yields zero entries (e.g. gist format changed), refuse
    to overwrite a previously-good in-memory map."""
    prior = {"iPhone17,3": "iPhone 16"}
    device_models_sync._synced = dict(prior)

    async def _stub():
        return "# only comments here\n"

    monkeypatch.setattr(device_models_sync, "_fetch_gist_text", _stub)
    count, detail = await device_models_sync.fetch_and_refresh()

    assert count == 0
    assert "zero entries" in detail
    assert device_models_sync._synced == prior


# --- disk cache ----------------------------------------------------------


def test_load_cached_from_disk_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    device_models_sync._synced = {}
    result = device_models_sync.load_cached_from_disk()
    assert result == {}


def test_load_cached_from_disk_reads_prior_sync(tmp_path, monkeypatch):
    import json
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "device_models_synced.json").write_text(
        json.dumps({"iPhone17,3": "iPhone 16"})
    )
    device_models_sync._synced = {}
    result = device_models_sync.load_cached_from_disk()
    assert result["iPhone17,3"] == "iPhone 16"


# --- /admin Cache-Control ------------------------------------------------


def test_admin_html_no_store_cache(client):
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "").lower()


# --- model_display -------------------------------------------------------


def test_model_display_known_mappings():
    from app.services.model_display import to_display_name
    assert to_display_name("cloudzap/auto") == "SS AI"
    assert to_display_name("onDevice/foundation-models") == "Apple Foundation Models"


def test_model_display_unknown_passes_through():
    from app.services.model_display import to_display_name
    assert to_display_name("anthropic/claude-haiku-4-5") == "anthropic/claude-haiku-4-5"


def test_model_display_none_and_empty():
    from app.services.model_display import to_display_name
    assert to_display_name(None) is None
    assert to_display_name("") == ""


def test_rich_endpoint_models_carry_display_name(client):
    import uuid as _uuid
    # Seed a managed-route meeting event so models[] has a row.
    body = {
        "event_type": "meeting_start",
        "device_id": str(_uuid.uuid4()),
        "model_id": "cloudzap/auto",
        "meeting_id": "model-display-test",
    }
    r = client.post("/v1/events/ping", json=body)
    assert r.status_code == 204
    resp = client.get(
        "/webhooks/admin/telemetry/rich?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    body = resp.json()
    managed = [m for m in body["models"] if m["model_id"] == "cloudzap/auto"]
    assert managed, "expected a cloudzap/auto row in models[]"
    assert managed[0]["display_name"] == "SS AI"
