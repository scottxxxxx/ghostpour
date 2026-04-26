"""End-to-end integration tests for CQ proxy endpoints."""

from unittest.mock import AsyncMock, patch

import httpx

from tests.conftest import _insert_user, _jwt_token


class TestCaptureTranscript:
    def test_capture_transcript_queued(self, client_with_cq, pro_user, mock_cq):
        """POST /v1/capture-transcript → queued, capture fires."""
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={
                "transcript": "Meeting discussion about Q2 goals.",
                "meeting_id": "meeting-123",
                "project": "Q2 Planning",
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_capture_transcript_forwards_identification_top_level(self, client_with_cq, pro_user, mock_cq):
        """user_identified / user_label / identification_source at top level → forwarded to cq.capture."""
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={
                "transcript": "...",
                "meeting_id": "m-1",
                "user_identified": True,
                "user_label": "Scott",
                "identification_source": "voice_id",
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        kwargs = mock_cq["capture"].call_args.kwargs
        assert kwargs["user_identified"] is True
        assert kwargs["user_label"] == "Scott"
        assert kwargs["identification_source"] == "voice_id"

    def test_capture_transcript_forwards_identification_metadata_dict(self, client_with_cq, pro_user, mock_cq):
        """Same fields nested under metadata: {...} → forwarded to cq.capture."""
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={
                "transcript": "...",
                "meeting_id": "m-2",
                "metadata": {
                    "user_identified": False,
                    "user_label": "Speaker 4",
                    "identification_source": "transcript_scan",
                },
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        kwargs = mock_cq["capture"].call_args.kwargs
        assert kwargs["user_identified"] is False
        assert kwargs["user_label"] == "Speaker 4"
        assert kwargs["identification_source"] == "transcript_scan"

    def test_capture_transcript_metadata_wins_over_top_level(self, client_with_cq, pro_user, mock_cq):
        """When both forms are sent, metadata dict takes precedence (matches ChatRequest behavior)."""
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={
                "transcript": "...",
                "meeting_id": "m-3",
                "user_label": "top-level",
                "metadata": {"user_label": "from-metadata"},
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        kwargs = mock_cq["capture"].call_args.kwargs
        assert kwargs["user_label"] == "from-metadata"


class TestQuiltProxy:
    def test_quilt_get_proxied(self, client_with_cq, pro_user):
        """GET /v1/quilt/{user_id} proxies to CQ."""
        mock_resp = httpx.Response(
            status_code=200,
            json={"patches": [], "count": 0},
            request=httpx.Request("GET", "http://cq-mock/v1/quilt/test"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}), \
             patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.get(
                f"/v1/quilt/{pro_user['user_id']}",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200

    def test_quilt_cross_user_forbidden(self, client_with_cq, pro_user):
        """User A trying to access user B's quilt → 403."""
        resp = client_with_cq.get(
            "/v1/quilt/someone-else",
            headers=pro_user["headers"],
        )
        assert resp.status_code == 403

    def test_assign_project_proxied(self, client_with_cq, pro_user):
        """POST /v1/meetings/{user_id}/{meeting_id}/assign-project proxies to CQ."""
        mock_resp = httpx.Response(
            status_code=200,
            json={"status": "ok", "patches_updated": 3},
            request=httpx.Request("POST", "http://cq-mock/v1/meetings/test/assign"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}), \
             patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.post(
                f"/v1/meetings/{pro_user['user_id']}/meeting-456/assign-project",
                json={"project_id": "proj-789", "project": "New Project"},
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        assert resp.json()["patches_updated"] == 3

    def test_assign_project_cross_user_forbidden(self, client_with_cq, pro_user):
        """Assigning another user's meeting → 403."""
        resp = client_with_cq.post(
            "/v1/meetings/someone-else/meeting-456/assign-project",
            json={"project_id": "proj-789"},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 403
