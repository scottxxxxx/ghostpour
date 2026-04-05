"""Tests for quilt proxy endpoints (graph visualization, prewarm)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord

TEST_USER_ID = "user-abc-123"

SVG_BODY = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
PNG_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # Fake PNG header


def _make_user(user_id: str = TEST_USER_ID) -> UserRecord:
    return UserRecord(
        id=user_id,
        apple_sub="sub_test",
        tier="pro",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )


@pytest.fixture
def client():
    """TestClient with auth dependency overridden to return a test user."""
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def wrong_user_client():
    """TestClient where the authenticated user doesn't match the URL user_id."""
    user = _make_user("different-user-999")
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _mock_cq_response(content: bytes, content_type: str, status: int = 200):
    """Create a mock httpx.Response for CQ."""
    return httpx.Response(
        status_code=status,
        content=content,
        headers={"content-type": content_type},
        request=httpx.Request("GET", "http://cq-mock/v1/quilt/test/graph"),
    )


# --- Happy path ---


@patch("app.routers.chat.get_settings")
def test_graph_svg(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    mock_resp = _mock_cq_response(SVG_BODY, "image/svg+xml")

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = instance

        resp = client.get(
            f"/v1/quilt/{TEST_USER_ID}/graph?format=svg",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert resp.content == SVG_BODY


@patch("app.routers.chat.get_settings")
def test_graph_png(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    mock_resp = _mock_cq_response(PNG_BODY, "image/png")

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = instance

        resp = client.get(
            f"/v1/quilt/{TEST_USER_ID}/graph?format=png",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == PNG_BODY


@patch("app.routers.chat.get_settings")
def test_graph_default_format_is_svg(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    mock_resp = _mock_cq_response(SVG_BODY, "image/svg+xml")

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = instance

        resp = client.get(
            f"/v1/quilt/{TEST_USER_ID}/graph",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"


# --- Auth / ownership ---


def test_graph_wrong_user_returns_403(wrong_user_client):
    resp = wrong_user_client.get(
        f"/v1/quilt/{TEST_USER_ID}/graph",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 403


# --- Validation ---


def test_graph_invalid_format_returns_400(client):
    resp = client.get(
        f"/v1/quilt/{TEST_USER_ID}/graph?format=webp",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 400
    assert "svg" in resp.json()["detail"].lower() or "png" in resp.json()["detail"].lower()


# --- CQ errors ---


@patch("app.routers.cq_proxy.get_settings")
def test_graph_cq_not_configured(mock_settings, client):
    mock_settings.return_value.cq_base_url = ""

    resp = client.get(
        f"/v1/quilt/{TEST_USER_ID}/graph",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 503


@patch("app.routers.chat.get_settings")
def test_graph_cq_timeout(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        MockClient.return_value = instance

        resp = client.get(
            f"/v1/quilt/{TEST_USER_ID}/graph",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 504


@patch("app.routers.chat.get_settings")
def test_graph_cq_unreachable(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=ConnectionError("refused"))
        MockClient.return_value = instance

        resp = client.get(
            f"/v1/quilt/{TEST_USER_ID}/graph",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 502


@patch("app.routers.chat.get_settings")
def test_graph_cq_returns_404(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    mock_resp = httpx.Response(
        status_code=404,
        json={"detail": "No quilt found"},
        request=httpx.Request("GET", "http://cq-mock/v1/quilt/test/graph"),
    )

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = instance

        resp = client.get(
            f"/v1/quilt/{TEST_USER_ID}/graph",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 404


# --- Prewarm endpoint ---


@patch("app.routers.chat.get_settings")
def test_prewarm_success(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    mock_resp = httpx.Response(
        status_code=200,
        json={"status": "warm", "profile": True, "entities": 42},
        request=httpx.Request("POST", "http://cq-mock/v1/prewarm"),
    )

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.request = AsyncMock(return_value=mock_resp)
        MockClient.return_value = instance

        resp = client.post(
            f"/v1/quilt/{TEST_USER_ID}/prewarm",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "warm"
    assert resp.json()["entities"] == 42


def test_prewarm_wrong_user_returns_403(wrong_user_client):
    resp = wrong_user_client.post(
        f"/v1/quilt/{TEST_USER_ID}/prewarm",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 403


@patch("app.routers.cq_proxy.get_settings")
def test_prewarm_cq_not_configured(mock_settings, client):
    mock_settings.return_value.cq_base_url = ""

    resp = client.post(
        f"/v1/quilt/{TEST_USER_ID}/prewarm",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 503


# --- Assign meeting project endpoint ---

TEST_MEETING_ID = "meeting-abc-456"


@patch("app.routers.chat.get_settings")
def test_assign_project_success(mock_settings, client):
    mock_settings.return_value.cq_base_url = "http://cq-mock"
    mock_settings.return_value.cq_app_id = "cloudzap"

    mock_resp = httpx.Response(
        status_code=200,
        json={"status": "ok", "patches_updated": 5},
        request=httpx.Request("POST", "http://cq-mock/v1/meetings/test/assign"),
    )

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.request = AsyncMock(return_value=mock_resp)
        MockClient.return_value = instance

        resp = client.post(
            f"/v1/meetings/{TEST_USER_ID}/{TEST_MEETING_ID}/assign-project",
            json={"project_id": "proj-new-789", "project": "New Project"},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.json()["patches_updated"] == 5


def test_assign_project_wrong_user_returns_403(wrong_user_client):
    resp = wrong_user_client.post(
        f"/v1/meetings/{TEST_USER_ID}/{TEST_MEETING_ID}/assign-project",
        json={"project_id": "proj-new-789"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 403


def test_assign_project_missing_project_id_returns_422(client):
    resp = client.post(
        f"/v1/meetings/{TEST_USER_ID}/{TEST_MEETING_ID}/assign-project",
        json={},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422
