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
        # ai_tier abstraction: clients should render this instead of raw model.
        # Free tier → "standard". Decoupled from model identity.
        assert data["ai_tier"] == "standard"

    def test_chat_ai_tier_advanced_for_pro(self, client, pro_user):
        """Pro tier → ai_tier=advanced regardless of which model answered."""
        resp = client.post("/v1/chat", json=chat_request(), headers=pro_user["headers"])
        assert resp.status_code == 200
        # ai_tier reflects the user's subscription, not the model name.
        # Pro stays "advanced" even if we serve them Haiku for cost reasons.
        assert resp.json()["ai_tier"] == "advanced"

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


class TestChatStreamTimeout:
    def test_stream_wall_clock_timeout_emits_error_event(self, client_with_cq, free_user, monkeypatch):
        """SSE stream that hangs past the wall-clock cap → stream_timeout error
        event, usage_log row with status="timeout", connection closes."""
        import asyncio
        from unittest.mock import patch
        from app.routers import chat as chat_module

        # Tighten the cap for the test so it fires fast.
        monkeypatch.setattr(chat_module, "_CHAT_STREAM_WALL_CLOCK_SECONDS", 0.2)

        async def slow_stream(_body):
            # Yield nothing for longer than the cap — emulates a stalled provider.
            await asyncio.sleep(2.0)
            yield {"type": "text", "text": "should never reach here", "done": False}

        with patch(
            "app.services.provider_router.ProviderRouter.route_stream",
            side_effect=lambda body: slow_stream(body),
        ):
            resp = client_with_cq.post(
                "/v1/chat",
                json=chat_request(stream=True, call_type="query"),
                headers=free_user["headers"],
            )

        assert resp.status_code == 200
        assert "stream_timeout" in resp.text
        assert "error" in resp.text

        # usage_log row written with status="timeout"
        import os
        import sqlite3
        db_path = os.environ["CZ_DATABASE_URL"].replace("sqlite+aiosqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status FROM usage_log WHERE user_id = ? AND status = 'timeout'",
            (free_user["user_id"],),
        ).fetchall()
        conn.close()
        assert len(rows) == 1


class TestProjectChatTeaser:
    def test_free_user_project_chat_returns_canned_no_llm(self, client, free_user, mock_provider):
        """Free tier (project_chat=teaser) → canned upsell text, no LLM call, no charge."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        # Canned response shape — looks like a normal chat bubble to iOS
        assert "Project Chat" in data["text"]
        assert "Plus" in data["text"]
        assert data["model"] == "ghostpour-canned"
        assert data["provider"] == "ghostpour"
        # ai_tier is the "free" sentinel for server-generated upsell bubbles,
        # distinct from "standard" / "advanced" badges on real AI responses.
        assert data["ai_tier"] == "free"
        assert data["input_tokens"] == 0
        assert data["output_tokens"] == 0
        assert data["cost"]["total_cost"] == 0.0
        # No LLM call should have happened
        mock_provider.assert_not_called()

    def test_pro_user_project_chat_uses_llm(self, client, pro_user, mock_provider):
        """Pro tier (project_chat=enabled) → real LLM call, not the canned upsell."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat"),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] != "ghostpour-canned"
        # Pro tier uses Anthropic via mock provider
        assert data["provider"] == "anthropic"
        mock_provider.assert_called_once()


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
