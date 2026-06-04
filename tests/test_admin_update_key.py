"""Admin /webhooks/admin/update-key Secret Manager persistence tests.

Pins the contract:
- Happy path: SM add_secret_version succeeds, response says persisted+SM
- First write: NotFound triggers auto-create then add_secret_version
- PermissionDenied: returns persisted=false with actionable detail
- No SM mapping for the env var: persisted=false, memory_only
- Unknown provider: 400
- Bad admin key: 403
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _post(client, body, admin_key="test-admin-key"):
    return client.post(
        "/webhooks/admin/update-key",
        json=body,
        headers={"X-Admin-Key": admin_key},
    )


def test_unknown_provider_returns_400(client):
    resp = _post(client, {"provider": "nonexistent", "api_key": "k"})
    assert resp.status_code == 400


def test_missing_admin_key_returns_403(client):
    resp = client.post(
        "/webhooks/admin/update-key",
        json={"provider": "openrouter", "api_key": "k"},
        headers={"X-Admin-Key": "wrong"},
    )
    assert resp.status_code == 403


def test_happy_path_persists_to_secret_manager(client):
    """add_secret_version succeeds → response is persisted+secret_manager."""
    with patch(
        "app.routers.webhooks._persist_to_secret_manager",
        return_value=(True, "Added new version to projects/test/secrets/openrouter-api-key"),
    ):
        resp = _post(client, {"provider": "openrouter", "api_key": "sk-or-v1-NEWNEWNEW"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted"] is True
    assert body["location"] == "secret_manager"
    assert body["secret_name"] == "openrouter-api-key"
    assert body["key_masked"].endswith("NEWS"[-4:]) or body["key_masked"].endswith("NEW")


def test_permission_denied_falls_back_to_memory_only(client):
    """When SA lacks SM write permission, response stays 200 but
    persisted=false and the detail names the IAM role to grant."""
    detail = (
        "Runtime SA lacks Secret Manager write permission: 403. "
        "Grant roles/secretmanager.admin on projects/cloudzap"
    )
    with patch(
        "app.routers.webhooks._persist_to_secret_manager",
        return_value=(False, detail),
    ):
        resp = _post(client, {"provider": "openrouter", "api_key": "sk-test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted"] is False
    assert body["location"] == "memory_only"
    assert "roles/secretmanager" in body["detail"]


def test_provider_without_sm_mapping_is_memory_only(client, monkeypatch):
    """Defensive path: if _SECRET_MANAGER_MAPPINGS doesn't have the
    env var, we never call SM and surface the misconfiguration to the
    operator."""
    # Drop openrouter mapping for this test.
    from app import config as cfg
    orig = dict(cfg._SECRET_MANAGER_MAPPINGS)
    cfg._SECRET_MANAGER_MAPPINGS.pop("CZ_OPENROUTER_API_KEY", None)
    try:
        resp = _post(client, {"provider": "openrouter", "api_key": "sk-test"})
    finally:
        cfg._SECRET_MANAGER_MAPPINGS.clear()
        cfg._SECRET_MANAGER_MAPPINGS.update(orig)
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted"] is False
    assert body["location"] == "memory_only"
    assert "No Secret Manager mapping" in body["detail"]


def test_in_memory_settings_updated_even_when_persistence_fails(client):
    """The endpoint's whole point of doing in-memory first is so the
    running process picks up the new key immediately even when SM
    persistence fails. Pin that ordering."""
    from app.main import app
    settings = app.state.settings
    with patch(
        "app.routers.webhooks._persist_to_secret_manager",
        return_value=(False, "anything"),
    ):
        _post(client, {"provider": "openrouter", "api_key": "sk-INMEMORYWIN"})
    assert settings.openrouter_api_key == "sk-INMEMORYWIN"


def test_persist_helper_auto_creates_secret_on_not_found(monkeypatch):
    """Unit test the helper directly: NotFound on add_secret_version
    triggers create_secret then a fresh add_secret_version."""
    from app.routers.webhooks import _persist_to_secret_manager

    # Build mock SM client + google.api_core exceptions
    from google.api_core.exceptions import NotFound

    client_mock = MagicMock()
    # First add_secret_version raises NotFound; second succeeds
    client_mock.add_secret_version.side_effect = [NotFound("nope"), MagicMock()]
    client_mock.create_secret.return_value = MagicMock()

    with patch(
        "google.cloud.secretmanager.SecretManagerServiceClient",
        return_value=client_mock,
    ), patch(
        "google.auth.default",
        return_value=(MagicMock(), "test-project"),
    ), patch(
        "app.secrets._resolve_project",
        return_value="test-project",
    ):
        ok, detail = _persist_to_secret_manager("openrouter-api-key", "sk-NEW")

    assert ok is True
    assert "Created secret" in detail
    client_mock.create_secret.assert_called_once()
    assert client_mock.add_secret_version.call_count == 2


def test_persist_helper_returns_actionable_permission_denied(monkeypatch):
    from app.routers.webhooks import _persist_to_secret_manager
    from google.api_core.exceptions import PermissionDenied

    client_mock = MagicMock()
    client_mock.add_secret_version.side_effect = PermissionDenied("403")

    with patch(
        "google.cloud.secretmanager.SecretManagerServiceClient",
        return_value=client_mock,
    ), patch(
        "google.auth.default",
        return_value=(MagicMock(), "test-project"),
    ), patch(
        "app.secrets._resolve_project",
        return_value="test-project",
    ):
        ok, detail = _persist_to_secret_manager("openrouter-api-key", "sk-NEW")

    assert ok is False
    assert "roles/secretmanager" in detail
