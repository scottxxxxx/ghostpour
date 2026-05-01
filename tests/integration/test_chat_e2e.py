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
        """User already past cap → 200 with empty text + budget_exhausted CTA.

        Replaces the legacy 429/allocation_exhausted path. The budget gate
        is now the sole authority for over-cap responses; one wire shape
        across 'already past cap' and 'this call would push past'.
        """
        resp = client.post("/v1/chat", json=chat_request(), headers=exhausted_user["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == ""
        cta = body["feature_state"]["cta"]
        assert cta["kind"] == "budget_exhausted"
        assert cta["action"] == "open_paywall"

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


class TestProtectedPromptsContextGate:
    """Server-side enforcement of requireMeetingContext on protected-prompts."""

    @staticmethod
    def _patch_protected_prompts(client, *, require_context: bool):
        client.app.state.remote_configs["protected-prompts"] = {
            "version": 99,
            "requireMeetingContext": require_context,
            "defaultPromptModes": [
                {"name": "Catch Me Up", "requiresContext": True, "systemPrompt": "..."},
                {"name": "Free Form", "requiresContext": False, "systemPrompt": "..."},
            ],
        }

    def test_gate_off_allows_context_required_prompt_without_meeting(self, client, free_user):
        self._patch_protected_prompts(client, require_context=False)
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="Catch Me Up"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200

    def test_gate_on_blocks_context_required_prompt_without_meeting(self, client, free_user):
        self._patch_protected_prompts(client, require_context=True)
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="Catch Me Up"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "context_required"

    def test_gate_on_with_meeting_id_allows(self, client, free_user):
        self._patch_protected_prompts(client, require_context=True)
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="Catch Me Up", meeting_id="meeting-abc"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200

    def test_gate_on_does_not_block_non_context_prompt(self, client, free_user):
        self._patch_protected_prompts(client, require_context=True)
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="Free Form"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200

    def test_gate_on_does_not_block_unknown_prompt_mode(self, client, free_user):
        self._patch_protected_prompts(client, require_context=True)
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="Some New Mode"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200


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


class TestProjectChatPolicy:
    """ProjectChat goes through the policy resolver (replaces PR #80 canned intercept).

    Default policy: gp_chat_flag="ssai_free_only", free_quota_per_month=1.
    ssai_free_only applies ssai semantics for Free tier (override + metered
    CTA) and logged_in semantics for paid tiers (respect BYOK). CTA only
    fires for Free + external + quota exhausted.
    """

    def test_free_user_project_chat_default_no_cta(self, client, free_user, mock_provider):
        """Free + default selected_model (ssai) → send_to_gp, no CTA.

        Under ssai_free_only, Free + ssai selected → send_to_gp, no CTA
        — they already opted into SS AI; no nag.
        """
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat"),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        mock_provider.assert_called_once()
        assert "feature_state" in data
        fs = data["feature_state"]
        assert fs["feature"] == "project_chat"
        assert fs["policy_mode"] == "ssai_free_only"
        assert "cta" not in fs

    def test_pro_user_project_chat_uses_llm_no_cta(self, client, pro_user, mock_provider):
        """Pro tier with SS AI selection → real LLM call, no CTA in feature_state."""
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat"),
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "anthropic"
        mock_provider.assert_called_once()
        fs = data["feature_state"]
        assert fs["feature"] == "project_chat"
        assert "cta" not in fs  # paid tier, no CTA
        assert "quota_remaining" not in fs  # not Free, no quota fields

    def test_free_external_first_send_decrements_freebie_then_cta_fires(
        self, client, free_user, mock_provider, tmp_db_path,
    ):
        """Reproduces the metering bug fix: Free + external first send burns
        the freebie (no CTA), second send fires the CTA.

        Pre-fix bug: decrement only fired on send_to_gp_with_cta verdict, but
        that verdict only fires when has_quota=False, which requires the
        counter to already be incremented. The freebie path (send_to_gp) never
        decremented, so the CTA never fired and Free users had unlimited
        Project Chat. This test pins the corrected behavior.
        """
        import sqlite3

        # First send (Free + external + has_quota=true → send_to_gp, no CTA, but burns freebie)
        resp = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat", metadata={"selected_model": "external"}),
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        fs = data["feature_state"]
        # No CTA on the freebie...
        assert "cta" not in fs
        # ...but quota counter visible to iOS for the pill update.
        assert fs["quota_total"] == 1
        assert fs["quota_remaining"] == 0  # 0/1 after the freebie consumed

        # DB confirms decrement.
        conn = sqlite3.connect(tmp_db_path)
        used = conn.execute(
            "SELECT project_chat_used_this_period FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()[0]
        conn.close()
        assert used == 1, f"expected counter at 1 after freebie, got {used}"

        # Second send (now has_quota=false → send_to_gp_with_cta).
        resp2 = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat", metadata={"selected_model": "external"}),
            headers=free_user["headers"],
        )
        assert resp2.status_code == 200
        fs2 = resp2.json()["feature_state"]
        assert fs2["cta"]["kind"] == "quota_exhausted"
        assert "Upgrade to Plus" in fs2["cta"]["text"]
        assert fs2["quota_remaining"] == 0

        # Counter must NOT double-decrement on the over-quota path. Third
        # send pins this — three CTA-served sends, counter still at 1.
        resp3 = client.post(
            "/v1/chat",
            json=chat_request(prompt_mode="ProjectChat", metadata={"selected_model": "external"}),
            headers=free_user["headers"],
        )
        assert resp3.status_code == 200
        assert resp3.json()["feature_state"]["cta"]["kind"] == "quota_exhausted"

        conn = sqlite3.connect(tmp_db_path)
        used_after = conn.execute(
            "SELECT project_chat_used_this_period FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()[0]
        conn.close()
        assert used_after == 1, (
            f"counter must cap at total=1 after freebie consumed; got used={used_after} "
            f"after 3 sends. Over-quota path must not double-decrement."
        )

    def test_free_ssai_path_does_not_meter(
        self, client, free_user, mock_provider, tmp_db_path,
    ):
        """Free + SS AI under ssai_free_only is intentionally unmetered.

        SS's design: Free user who picked SS AI 'already opted in' — no
        quota burn, no CTA, no nag. Only the BYOK-override path is metered.
        Pin that behavior so future refactors don't accidentally meter it.
        """
        import sqlite3

        for _ in range(3):
            resp = client.post(
                "/v1/chat",
                json=chat_request(prompt_mode="ProjectChat", metadata={"selected_model": "ssai"}),
                headers=free_user["headers"],
            )
            assert resp.status_code == 200
            assert "cta" not in resp.json()["feature_state"]

        conn = sqlite3.connect(tmp_db_path)
        used = conn.execute(
            "SELECT project_chat_used_this_period FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()[0]
        conn.close()
        assert used == 0, (
            f"Free + SS AI should not decrement quota; got used={used} after 3 sends"
        )


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
