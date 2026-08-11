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

    def test_quilt_get_forwards_query_string(self, client_with_cq, pro_user):
        """?since=...&delta=true reaches CQ verbatim — dropping it made every
        delta poll return the full quilt."""
        mock_resp = httpx.Response(
            status_code=200,
            json={"facts": [], "deleted": []},
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
                f"/v1/quilt/{pro_user['user_id']}?since=2026-06-11T02:55:17.309191Z&delta=true",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        called_path = instance.request.call_args.args[1]
        assert "since=2026-06-11T02%3A55%3A17.309191Z" in called_path or "since=2026-06-11T02:55:17.309191Z" in called_path
        assert "delta=true" in called_path

    def test_quilt_get_no_query_unchanged(self, client_with_cq, pro_user):
        """No query string → path proxied bare, no trailing '?'."""
        mock_resp = httpx.Response(
            status_code=200,
            json={"facts": [], "deleted": []},
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
        called_path = instance.request.call_args.args[1]
        assert called_path == f"/v1/quilt/{pro_user['user_id']}"

    def test_insights_body_passes_through_clean(self, client_with_cq, pro_user):
        """GET /v1/quilt/{user_id}/insights: CQ's body reaches the client
        value-for-value. The middlebox risks that bit the quilt reads
        (eaten query params, eaten metadata keys) do not apply to a
        no-param GET, but the passthrough is asserted anyway so a future
        "helpful" transform fails a test instead of a device."""
        payload = {
            "user_id": pro_user["user_id"],
            "follow_up": {
                "kind": "overdue_commitment",
                "text": "You owe Dana the Q3 forecast",
                "patch_id": "p-777",
                "unknown_future_key": {"nested": True},
            },
        }
        mock_resp = httpx.Response(
            status_code=200,
            json=payload,
            request=httpx.Request("GET", "http://cq-mock/v1/quilt/test/insights"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}),              patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.get(
                f"/v1/quilt/{pro_user['user_id']}/insights",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        assert resp.json() == payload
        called_path = instance.request.call_args.args[1]
        assert called_path == f"/v1/quilt/{pro_user['user_id']}/insights"

    def test_insights_null_follow_up_survives(self, client_with_cq, pro_user):
        """follow_up: null is a real answer (nothing to surface), not a
        key to strip. Per the doc 16 lesson, null means CQ says none,
        and the client must receive the null rather than an absent key."""
        payload = {"user_id": pro_user["user_id"], "follow_up": None}
        mock_resp = httpx.Response(
            status_code=200,
            json=payload,
            request=httpx.Request("GET", "http://cq-mock/v1/quilt/test/insights"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}),              patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.get(
                f"/v1/quilt/{pro_user['user_id']}/insights",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "follow_up" in body
        assert body["follow_up"] is None

    def test_insights_upstream_404_passes_through(self, client_with_cq, pro_user):
        """Until CQ PR #227 deploys, CQ answers 404 on this path. That
        status must reach the client as CQ's answer, which is exactly why
        carrying the route first is safe: 404-from-upstream and
        404-from-a-missing-route look identical to a device, but only the
        first one fixes itself when CQ ships."""
        mock_resp = httpx.Response(
            status_code=404,
            json={"detail": "Not Found"},
            request=httpx.Request("GET", "http://cq-mock/v1/quilt/test/insights"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}),              patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.get(
                f"/v1/quilt/{pro_user['user_id']}/insights",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 404

    def test_insights_cross_user_forbidden(self, client_with_cq, pro_user):
        resp = client_with_cq.get(
            "/v1/quilt/someone-else/insights",
            headers=pro_user["headers"],
        )
        assert resp.status_code == 403

    def test_complete_patch_proxied_with_status_passthrough(self, client_with_cq, pro_user):
        """POST .../patches/{id}/complete forwards the body and passes CQ's
        status codes through verbatim — 409 (already completed / lost race
        to auto-close) must reach the device as 409."""
        mock_resp = httpx.Response(
            status_code=409,
            json={"detail": "already completed"},
            request=httpx.Request("POST", "http://cq-mock/v1/quilt/u/patches/p/complete"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}), \
             patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.post(
                f"/v1/quilt/{pro_user['user_id']}/patches/abc-123/complete",
                json={"source": "tap"},
                headers=pro_user["headers"],
            )
        assert resp.status_code == 409
        assert resp.json() == {"detail": "already completed"}
        called_path = instance.request.call_args.args[1]
        assert called_path == f"/v1/quilt/{pro_user['user_id']}/patches/abc-123/complete"
        assert instance.request.call_args.kwargs["json"] == {"source": "tap"}

    def test_complete_patch_no_body(self, client_with_cq, pro_user):
        """The JSON body is optional — a bare POST forwards with no body."""
        mock_resp = httpx.Response(
            status_code=200,
            json={"status": "completed"},
            request=httpx.Request("POST", "http://cq-mock/v1/quilt/u/patches/p/complete"),
        )
        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}), \
             patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(return_value=mock_resp)
            MockClient.return_value = instance

            resp = client_with_cq.post(
                f"/v1/quilt/{pro_user['user_id']}/patches/abc-123/complete",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        assert instance.request.call_args.kwargs["json"] is None

    def test_complete_patch_cross_user_forbidden(self, client_with_cq, pro_user):
        resp = client_with_cq.post(
            "/v1/quilt/someone-else/patches/abc-123/complete",
            headers=pro_user["headers"],
        )
        assert resp.status_code == 403

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


class TestReassignSpeaker:
    def test_reassign_speaker_to_self_proxied(self, client_with_cq, pro_user):
        """POST /v1/quilt/{user_id}/reassign-speaker (to_self) proxies to CQ verbatim."""
        captured = {}
        cq_response = {"patches_updated": 7, "connections_updated": 3, "entities_merged": 2}
        mock_resp = httpx.Response(
            status_code=200,
            json=cq_response,
            request=httpx.Request("POST", "http://cq-mock/v1/quilt/test/reassign-speaker"),
        )

        async def fake_request(method, path, json=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = json
            return mock_resp

        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}), \
             patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(side_effect=fake_request)
            MockClient.return_value = instance

            resp = client_with_cq.post(
                f"/v1/quilt/{pro_user['user_id']}/reassign-speaker",
                json={
                    "from_labels": [
                        {"label": "Speaker 4", "meeting_id": "m-aaa"},
                        {"label": "Unknown 1", "meeting_id": "m-bbb"},
                    ],
                    "to_self": True,
                },
                headers=pro_user["headers"],
            )

        assert resp.status_code == 200
        assert resp.json() == cq_response
        assert captured["method"] == "POST"
        assert captured["path"] == f"/v1/quilt/{pro_user['user_id']}/reassign-speaker"
        # to_person_id is None and stripped; body forwarded verbatim otherwise
        assert captured["body"] == {
            "from_labels": [
                {"label": "Speaker 4", "meeting_id": "m-aaa"},
                {"label": "Unknown 1", "meeting_id": "m-bbb"},
            ],
            "to_self": True,
        }

    def test_reassign_speaker_to_person_id_proxied(self, client_with_cq, pro_user):
        """POST reassign-speaker with to_person_id forwards person id, not to_self."""
        captured = {}
        mock_resp = httpx.Response(
            status_code=200,
            json={"patches_updated": 1, "connections_updated": 0, "entities_merged": 1},
            request=httpx.Request("POST", "http://cq-mock/v1/quilt/test/reassign-speaker"),
        )

        async def fake_request(method, path, json=None, headers=None):
            captured["body"] = json
            return mock_resp

        with patch("app.services.context_quilt._get_auth_headers", new_callable=AsyncMock, return_value={"Authorization": "Bearer mock"}), \
             patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.request = AsyncMock(side_effect=fake_request)
            MockClient.return_value = instance

            resp = client_with_cq.post(
                f"/v1/quilt/{pro_user['user_id']}/reassign-speaker",
                json={
                    "from_labels": [{"label": "Speaker 2", "meeting_id": "m-xyz"}],
                    "to_person_id": "person-uuid-123",
                },
                headers=pro_user["headers"],
            )

        assert resp.status_code == 200
        assert captured["body"] == {
            "from_labels": [{"label": "Speaker 2", "meeting_id": "m-xyz"}],
            "to_person_id": "person-uuid-123",
        }

    def test_reassign_speaker_cross_user_forbidden(self, client_with_cq, pro_user):
        """Reassigning speakers in another user's quilt → 403."""
        resp = client_with_cq.post(
            "/v1/quilt/someone-else/reassign-speaker",
            json={
                "from_labels": [{"label": "Speaker 1", "meeting_id": "m-1"}],
                "to_self": True,
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 403

    def test_reassign_speaker_requires_a_target(self, client_with_cq, pro_user):
        """Neither to_self nor to_person_id → 422 from validation, no CQ call."""
        resp = client_with_cq.post(
            f"/v1/quilt/{pro_user['user_id']}/reassign-speaker",
            json={"from_labels": [{"label": "Speaker 3", "meeting_id": "m-1"}]},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 422

    def test_reassign_speaker_rejects_both_targets(self, client_with_cq, pro_user):
        """Both to_self=true AND to_person_id → 422."""
        resp = client_with_cq.post(
            f"/v1/quilt/{pro_user['user_id']}/reassign-speaker",
            json={
                "from_labels": [{"label": "Speaker 3", "meeting_id": "m-1"}],
                "to_self": True,
                "to_person_id": "person-1",
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 422

    def test_reassign_speaker_rejects_empty_from_labels(self, client_with_cq, pro_user):
        """Empty from_labels → 422."""
        resp = client_with_cq.post(
            f"/v1/quilt/{pro_user['user_id']}/reassign-speaker",
            json={"from_labels": [], "to_self": True},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 422

    def test_reassign_speaker_rejects_label_missing_meeting_id(self, client_with_cq, pro_user):
        """from_labels item without meeting_id → 422 (Pydantic validation, no CQ call)."""
        resp = client_with_cq.post(
            f"/v1/quilt/{pro_user['user_id']}/reassign-speaker",
            json={"from_labels": [{"label": "Speaker 3"}], "to_self": True},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 422
