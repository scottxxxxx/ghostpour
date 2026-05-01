"""End-to-end tests for the pre-call budget gate + context-cap gate.

Pin the wire shape SS will consume:
- 200 + empty text + feature_state.cta {kind: budget_exhausted, action: open_paywall}
  for the budget block.
- 413 + feature_state.cta {kind: context_too_large, action: trim_context} +
  details {max_tokens, actual_tokens, tokenizer} for the context-cap block.
- Canned report (report_status=placeholder_budget_blocked, is_editable=false)
  when a meeting report would exceed budget.

Cost estimator is patched per-test so we don't depend on LiteLLM pricing
data being loaded in the test environment.
"""

import sqlite3
from unittest.mock import patch

from tests.conftest import chat_request


def _force_cost(usd: float):
    """Patch the cost estimator to return a fixed dollar amount. Returned
    context manager covers both call sites (chat.py + reports.py) since
    they import budget_gate the same way."""
    return patch(
        "app.services.budget_gate.estimate_call_cost_usd",
        return_value=usd,
    )


def _set_monthly_used(tmp_db_path: str, user_id: str, used_usd: float):
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE users SET monthly_used_usd = ? WHERE id = ?",
        (used_usd, user_id),
    )
    conn.commit()
    conn.close()


class TestChatBudgetGate:
    def test_free_user_under_budget_is_served(self, client, free_user, mock_provider):
        """Sanity: tiny estimated cost + small monthly_used → request goes through."""
        with _force_cost(0.001):
            resp = client.post(
                "/v1/chat",
                json=chat_request(),
                headers=free_user["headers"],
            )
        assert resp.status_code == 200
        body = resp.json()
        # Real LLM response, not a block — text non-empty, no budget_exhausted CTA.
        assert body["text"]  # non-empty
        cta = (body.get("feature_state") or {}).get("cta")
        assert cta is None or cta.get("kind") != "budget_exhausted"

    def test_free_user_over_budget_returns_cta_block(
        self, client, free_user, mock_provider, tmp_db_path,
    ):
        """Free user near cap + costly estimate → 200 with empty text and
        budget_exhausted CTA. No assistant bubble persistence on the iOS
        side (per SS contract); we just verify wire shape here."""
        # Free's cap is $0.35, overage tolerance $0.05 → ceiling $0.40.
        # Set used=$0.34, estimate=$0.10 → would land at $0.44. Over.
        _set_monthly_used(tmp_db_path, free_user["user_id"], 0.34)
        with _force_cost(0.10):
            resp = client.post(
                "/v1/chat",
                json=chat_request(),
                headers=free_user["headers"],
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == ""
        fs = body["feature_state"]
        assert fs["cta"]["kind"] == "budget_exhausted"
        assert fs["cta"]["action"] == "open_paywall"
        # Credit fields obfuscate dollars at the wire boundary.
        # Free $0.35 = 3500 credits, $0.34 used = 3400 used, 100 remaining.
        assert fs["credits_total"] == 3500
        assert fs["credits_remaining"] == 100

    def test_free_user_within_overage_tolerance_is_served(
        self, client, free_user, mock_provider, tmp_db_path,
    ):
        """$0.34 used + $0.05 estimated = $0.39, ≤ $0.40 ceiling. Allowed."""
        _set_monthly_used(tmp_db_path, free_user["user_id"], 0.34)
        with _force_cost(0.05):
            resp = client.post(
                "/v1/chat",
                json=chat_request(),
                headers=free_user["headers"],
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"]  # served, not blocked

    def test_pro_user_unlimited_skips_gate(self, client, pro_user, mock_provider):
        """Pro is unlimited (effective_limit=-1). Even an absurd estimate
        must not block — tier signals 'don't gate this user.'"""
        with _force_cost(99999.0):
            resp = client.post(
                "/v1/chat",
                json=chat_request(),
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"]  # served

    def test_block_response_has_no_persisted_usage(
        self, client, free_user, mock_provider, tmp_db_path,
    ):
        """Critical: a blocked call must NOT increment the cost ledger.
        Otherwise blocked Free users would burn quota anyway."""
        _set_monthly_used(tmp_db_path, free_user["user_id"], 0.34)
        with _force_cost(0.10):
            client.post(
                "/v1/chat",
                json=chat_request(),
                headers=free_user["headers"],
            )
        conn = sqlite3.connect(tmp_db_path)
        used_after = conn.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()[0]
        conn.close()
        # Counter unchanged at 0.34 (the value we set pre-call).
        assert abs(used_after - 0.34) < 1e-9


class TestContextCapGate:
    def test_project_chat_oversized_context_returns_413(
        self, client, free_user, mock_provider,
    ):
        """Free's max_input_tokens is 50K. system_prompt big enough to
        push (sys + user) / 4 over 50K → 413 with context_too_large."""
        # 50_001 tokens via chars/4 → need 200_004 chars
        big_prompt = "a" * 200_004
        resp = client.post(
            "/v1/chat",
            json=chat_request(
                prompt_mode="ProjectChat",
                system_prompt=big_prompt,
                user_content="hi",
            ),
            headers=free_user["headers"],
        )
        assert resp.status_code == 413
        detail = resp.json()["detail"]
        assert detail["code"] == "context_too_large"
        cta = detail["feature_state"]["cta"]
        assert cta["kind"] == "context_too_large"
        assert cta["action"] == "trim_context"
        details = detail["feature_state"]["details"]
        assert details["max_tokens"] == 50_000
        assert details["actual_tokens"] > 50_000
        assert details["tokenizer"] == "chars_div_4"

    def test_non_project_chat_skips_context_cap(
        self, client, free_user, mock_provider,
    ):
        """The cap only applies to Project Chat — Free Form / Catch Me Up
        / etc. don't have this gate (different UX, different intent)."""
        big_prompt = "a" * 200_004
        with _force_cost(0.001):
            resp = client.post(
                "/v1/chat",
                json=chat_request(
                    system_prompt=big_prompt,
                    user_content="hi",
                ),
                headers=free_user["headers"],
            )
        # Not a 413 — no Project Chat cap. Just normal flow.
        assert resp.status_code == 200

    def test_within_context_cap_is_served(
        self, client, free_user, mock_provider,
    ):
        """Just under 50K — request goes through normally."""
        # ~49K tokens → 196_000 chars
        prompt = "a" * 196_000
        with _force_cost(0.001):
            resp = client.post(
                "/v1/chat",
                json=chat_request(
                    prompt_mode="ProjectChat",
                    metadata={"selected_model": "ssai"},
                    system_prompt=prompt,
                    user_content="hi",
                ),
                headers=free_user["headers"],
            )
        assert resp.status_code == 200

    def test_plus_user_higher_cap(
        self, client, plus_user, mock_provider,
    ):
        """Plus's cap is 150K. A prompt that blows Free's cap should still
        fit Plus. Pin the per-tier differentiation."""
        # 60K tokens → 240_000 chars. Over Free (50K) but under Plus (150K).
        prompt = "a" * 240_000
        with _force_cost(0.001):
            resp = client.post(
                "/v1/chat",
                json=chat_request(
                    prompt_mode="ProjectChat",
                    metadata={"selected_model": "ssai"},
                    system_prompt=prompt,
                    user_content="hi",
                ),
                headers=plus_user["headers"],
            )
        assert resp.status_code == 200
