"""End-to-end integration tests for POST /v1/chat.

These tests exercise the full request flow: JWT auth → tier lookup →
rate limiting → quota check → CQ recall → provider routing → cost
recording → usage logging → CQ capture → response with allocation headers.

External services (LLM providers, CQ, pricing) are mocked.
The database and tier config are real.
"""

import pytest

from tests.conftest import chat_request


# ---------------------------------------------------------------------------
# Basic chat flow
# ---------------------------------------------------------------------------


class TestChatBasicFlow:
    def test_chat_success_auto_model(self, client, free_user):
        """Free tier user, auto model → resolves to Haiku, returns 200."""
        resp = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
        assert resp.status_code == 200
        data = resp.json()
        assert "text" in data
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-haiku-4-5-20251001"

    def test_chat_allocation_headers_present(self, client, free_user):
        """Response includes allocation tracking headers."""
        resp = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
        assert resp.status_code == 200
        assert "x-allocation-percent" in resp.headers
        assert "x-monthly-used" in resp.headers
        assert "x-monthly-limit" in resp.headers

    def test_chat_no_auth_returns_401(self, client):
        """Request without auth token → 401."""
        resp = client.post("/v1/chat", json=chat_request())
        # HTTPBearer returns 403 when no credentials provided
        assert resp.status_code in (401, 403)

    def test_chat_invalid_token_returns_401(self, client):
        """Request with bad JWT → 401."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(),
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Quota / allocation enforcement
# ---------------------------------------------------------------------------


class TestChatQuota:
    def test_chat_quota_exhausted(self, client, exhausted_user):
        """User with monthly_used >= limit → 429."""
        resp = client.post("/v1/chat", json=chat_request(), headers=exhausted_user["headers"])
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "allocation_exhausted"

    def test_chat_allocation_warning_at_80_percent(self, client, tmp_db_path):
        """User near 80% allocation → X-Allocation-Warning header."""
        from tests.conftest import _insert_user, _jwt_token
        user_id = "test-near-limit"
        _insert_user(tmp_db_path, user_id=user_id, tier="free", monthly_limit=0.35, monthly_used=0.30)
        headers = {"Authorization": f"Bearer {_jwt_token(user_id)}"}
        resp = client.post("/v1/chat", json=chat_request(), headers=headers)
        assert resp.status_code == 200
        assert resp.headers.get("x-allocation-warning") == "true"


# ---------------------------------------------------------------------------
# Model access enforcement
# ---------------------------------------------------------------------------


class TestChatModelAccess:
    def test_free_tier_blocked_provider(self, client, free_user):
        """Free tier requesting a provider not in allowed_providers → 403."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(provider="openai", model="gpt-5.2"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 403

    def test_free_tier_blocked_model(self, client, free_user):
        """Free tier requesting a model not in allowed_models → 403."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(provider="anthropic", model="claude-sonnet-4-6"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Context Quilt integration
# ---------------------------------------------------------------------------


class TestChatCQ:
    def test_cq_recall_enabled_injects_context(self, client_with_cq, pro_user, mock_cq):
        """Pro tier with context_quilt=true → CQ recall called, context injected."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=True),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["recall"].assert_called_once()
        # CQ matched entities header
        assert "x-cq-matched" in resp.headers

    def test_cq_capture_fires_on_enabled(self, client_with_cq, pro_user, mock_cq):
        """Pro tier, CQ enabled, non-skip mode → capture fires."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=True, prompt_mode="Assist"),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["capture"].assert_called_once()

    def test_cq_capture_skipped_for_post_meeting_chat(self, client_with_cq, pro_user, mock_cq):
        """PostMeetingChat mode → capture should NOT fire."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=True, prompt_mode="PostMeetingChat"),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["capture"].assert_not_called()

    def test_cq_capture_skipped_during_active_session(self, client_with_cq, pro_user, mock_cq):
        """Active session (session_duration_sec set) → capture should NOT fire."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=True, session_duration_sec=120),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["capture"].assert_not_called()

    def test_cq_teaser_returns_gated_header(self, client_with_cq, plus_user, mock_cq):
        """Standard tier (CQ=teaser) → recall runs, X-CQ-Gated header set, no injection."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=True),
            headers=plus_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["recall"].assert_called_once()
        assert resp.headers.get("x-cq-gated") == "true"

    def test_cq_disabled_skips_recall(self, client_with_cq, free_user, mock_cq):
        """Free tier (CQ=disabled) → recall NOT called even with context_quilt=true."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=True),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["recall"].assert_not_called()

    def test_cq_not_requested(self, client_with_cq, pro_user, mock_cq):
        """Pro tier but context_quilt=false → recall NOT called."""
        resp = client_with_cq.post(
            "/v1/chat",
            json=chat_request(context_quilt=False),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        mock_cq["recall"].assert_not_called()


# ---------------------------------------------------------------------------
# Usage logging
# ---------------------------------------------------------------------------


class TestChatUsageLogging:
    def test_usage_logged_to_db(self, client, free_user, tmp_db_path):
        """Successful chat request creates a usage_log row."""
        resp = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
        assert resp.status_code == 200

        import sqlite3
        conn = sqlite3.connect(tmp_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM usage_log WHERE user_id = ?",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["provider"] == "anthropic"
        assert row["status"] == "success"

    def test_ss_fields_logged(self, client, free_user, tmp_db_path):
        """SS-specific fields (call_type, prompt_mode, etc.) are persisted."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(
                call_type="query",
                prompt_mode="Assist",
                session_duration_sec=300,
            ),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200

        import sqlite3
        conn = sqlite3.connect(tmp_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT call_type, prompt_mode, session_duration_sec FROM usage_log WHERE user_id = ?",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row["call_type"] == "query"
        assert row["prompt_mode"] == "Assist"
        assert row["session_duration_sec"] == 300


# ---------------------------------------------------------------------------
# Auto model resolution
# ---------------------------------------------------------------------------


class TestChatAutoModel:
    def test_auto_resolves_to_tier_default(self, client, free_user, mock_provider):
        """provider=auto, model=auto → resolved to tier's default_model."""
        resp = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
        assert resp.status_code == 200
        # The mock provider was called with the resolved model
        call_args = mock_provider.call_args
        resolved_request = call_args[0][0]  # First positional arg is ChatRequest
        assert resolved_request.provider == "anthropic"
        assert resolved_request.model == "claude-haiku-4-5-20251001"
