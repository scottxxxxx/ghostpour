"""Tests for metadata.language forwarding on cq.capture.

CQ writes extracted memory text in the language given by metadata.language
on POST /v1/memory (BCP-47, full tags fine); absent, it infers from the
speaker's words, which guesses wrong in mixed-language meetings. GP sources
the language per capture path:
- /v1/capture-transcript: metadata.language from the app, falling back to
  the request's Accept-Language header
- chat after_llm hook: metadata.language falling back to the body locale
- admin capture: the user's most recent telemetry app_locale
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from app.routers.cq_proxy import _primary_language_tag


class TestPrimaryLanguageTag:
    def test_full_tag_with_quality_list(self):
        assert _primary_language_tag("es-US,es-419;q=0.9,es;q=0.8") == "es-US"

    def test_bare_language(self):
        assert _primary_language_tag("ja") == "ja"

    def test_english_is_a_real_answer(self):
        assert _primary_language_tag("en-US,en;q=0.9") == "en-US"

    def test_none_and_empty(self):
        assert _primary_language_tag(None) is None
        assert _primary_language_tag("") is None


class TestCaptureTranscriptLanguage:
    def test_metadata_language_wins(self, client_with_cq, pro_user, mock_cq):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={
                "transcript": "...",
                "meeting_id": "m-lang-1",
                "metadata": {"language": "es-MX"},
            },
            headers={**pro_user["headers"], "Accept-Language": "en-US"},
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["language"] == "es-MX"

    def test_falls_back_to_accept_language(self, client_with_cq, pro_user, mock_cq):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-lang-2"},
            headers={**pro_user["headers"], "Accept-Language": "es-US,es-419;q=0.9"},
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["language"] == "es-US"

    def test_absent_everywhere_is_none(self, client_with_cq, pro_user, mock_cq):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-lang-3"},
            headers={k: v for k, v in pro_user["headers"].items()},
        )
        assert resp.status_code == 200
        # TestClient may add its own Accept-Language-free defaults; the key
        # assertion is that we never invent a language.
        assert mock_cq["capture"].call_args.kwargs["language"] in (None, "")


class TestChatHookLanguage:
    def test_locale_field_used_when_no_metadata_language(
        self, client_with_cq, pro_user, mock_cq
    ):
        resp = client_with_cq.post(
            "/v1/chat",
            json={
                "provider": "auto",
                "model": "auto",
                "system_prompt": "You are helpful.",
                "user_content": "Remember that my favorite color is purple.",
                "context_quilt": True,
                "locale": "es",
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].await_count == 1
        assert mock_cq["capture"].call_args.kwargs["language"] == "es"

    def test_metadata_language_beats_locale(
        self, client_with_cq, pro_user, mock_cq
    ):
        resp = client_with_cq.post(
            "/v1/chat",
            json={
                "provider": "auto",
                "model": "auto",
                "system_prompt": "You are helpful.",
                "user_content": "Remember that my favorite color is purple.",
                "context_quilt": True,
                "locale": "en",
                "metadata": {"language": "ja-JP"},
            },
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["language"] == "ja-JP"


class TestAdminCaptureLanguage:
    def _insert_ping(self, db_path: str, user_id: str, app_locale: str):
        con = sqlite3.connect(db_path)
        con.execute(
            """INSERT INTO telemetry_events
               (id, event_type, device_id, user_id, received_at, app_locale)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                "app_start",
                "device-1",
                user_id,
                datetime.now(timezone.utc).isoformat(),
                app_locale,
            ),
        )
        con.commit()
        con.close()

    def test_sources_language_from_latest_telemetry(
        self, client_with_cq, pro_user, mock_cq, tmp_db_path
    ):
        self._insert_ping(tmp_db_path, pro_user["user_id"], "es_US")
        resp = client_with_cq.post(
            "/webhooks/admin/capture-transcript",
            json={
                "user_id": pro_user["user_id"],
                "transcript": "...",
                "meeting_id": "m-admin-lang",
            },
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["language"] == "es-US"

    def test_no_telemetry_means_no_language(
        self, client_with_cq, pro_user, mock_cq
    ):
        resp = client_with_cq.post(
            "/webhooks/admin/capture-transcript",
            json={
                "user_id": pro_user["user_id"],
                "transcript": "...",
                "meeting_id": "m-admin-nolang",
            },
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["language"] is None


class TestCaptureServiceWireShape:
    def test_language_lands_in_metadata(self, app_env):
        """capture() puts language into the /v1/memory metadata object."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.services import context_quilt as cq

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(cq, "_get_client", return_value=mock_client), patch.object(
            cq, "_get_auth_headers", new_callable=AsyncMock, return_value={}
        ):
            asyncio.run(cq.capture(
                user_id="u1",
                interaction_type="meeting_transcript",
                content="hola",
                language="es-US",
            ))

        body = mock_client.post.call_args.kwargs["json"]
        assert body["metadata"]["language"] == "es-US"

    def test_no_language_key_when_absent(self, app_env):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.services import context_quilt as cq

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(cq, "_get_client", return_value=mock_client), patch.object(
            cq, "_get_auth_headers", new_callable=AsyncMock, return_value={}
        ):
            asyncio.run(cq.capture(
                user_id="u1",
                interaction_type="meeting_transcript",
                content="hello",
                project="Test",
            ))

        body = mock_client.post.call_args.kwargs["json"]
        assert "language" not in body["metadata"]
