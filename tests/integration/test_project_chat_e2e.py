"""Integration tests for the Project Chat policy endpoint and /v1/chat path."""

import sqlite3

from tests.conftest import _insert_user, _jwt_token


PREFLIGHT_PATH = "/v1/features/project-chat/check"


class TestProjectChatPreflight:
    def test_unauthenticated_returns_login_required(self, client):
        """No JWT + external selected → verdict=login_required under ssai mode."""
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Default flag is "ssai" — non-logged-in with external selected → login_required
        assert body["verdict"] == "login_required"
        assert body["cta"]["kind"] == "login_required"

    def test_plus_user_external_model_routes_to_gp_under_ssai(self, client, tmp_db_path):
        """Plus user with external selected → send_to_gp under ssai mode (override).

        ssai mode forces routing through GP regardless of the user's selected
        model. This is the override behavior — distinct from logged_in/all
        modes which respect the user's choice.
        """
        _insert_user(tmp_db_path, user_id="plus-pc", tier="plus", monthly_limit=-1)
        headers = {"Authorization": f"Bearer {_jwt_token('plus-pc')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp"
        # No CTA for paid users even when overridden
        assert "cta" not in body

    def test_plus_user_ssai_model_routes_to_gp(self, client, tmp_db_path):
        """Plus user with SS AI selected → send_to_gp."""
        _insert_user(tmp_db_path, user_id="plus-ssai", tier="plus", monthly_limit=-1)
        headers = {"Authorization": f"Bearer {_jwt_token('plus-ssai')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "ssai"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp"
        # No CTA for paid users
        assert "cta" not in body

    def test_free_user_ssai_selected_no_cta_under_ssai_mode(self, client, tmp_db_path):
        """Free user with SS AI picked → send_to_gp, NO CTA (they already opted in)."""
        _insert_user(tmp_db_path, user_id="free-ssai", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('free-ssai')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "ssai"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp"
        assert "cta" not in body

    def test_free_user_external_with_quota_no_cta(self, client, tmp_db_path):
        """Free + external + quota remaining → send_to_gp under ssai mode (the freebie)."""
        _insert_user(tmp_db_path, user_id="free-ext-quota", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('free-ext-quota')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Under ssai mode + quota remaining + external → send_to_gp (no CTA yet)
        assert body["verdict"] == "send_to_gp"
        assert "cta" not in body
        assert body["quota_remaining"] == 1
        assert body["quota_total"] == 1

    def test_free_user_external_quota_exhausted_gets_cta(self, client, tmp_db_path):
        """Free + external + quota exhausted → send_to_gp_with_cta (the metered gate)."""
        from app.services.project_chat_quota import current_period_utc
        _insert_user(tmp_db_path, user_id="free-ext-burned", tier="free", monthly_limit=0.35)
        # Burn the user's quota for the current period
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            "UPDATE users SET project_chat_used_this_period = 1, project_chat_period = ? WHERE id = ?",
            (current_period_utc(), "free-ext-burned"),
        )
        conn.commit()
        conn.close()

        headers = {"Authorization": f"Bearer {_jwt_token('free-ext-burned')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp_with_cta"
        assert body["cta"]["kind"] == "quota_exhausted"
        assert body["quota_remaining"] == 0
        assert body["quota_total"] == 1

    def test_preflight_does_not_decrement_quota(self, client, tmp_db_path):
        """Calling preflight repeatedly does not change the counter."""
        _insert_user(tmp_db_path, user_id="free-no-decrement", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('free-no-decrement')}"}
        for _ in range(3):
            resp = client.post(
                PREFLIGHT_PATH,
                json={"selected_model": "ssai"},
                headers=headers,
            )
            assert resp.status_code == 200

        conn = sqlite3.connect(tmp_db_path)
        used = conn.execute(
            "SELECT project_chat_used_this_period FROM users WHERE id = ?",
            ("free-no-decrement",),
        ).fetchone()[0]
        conn.close()
        assert used == 0


class TestProjectChatTierUpgradeZerosCounter:
    def test_free_to_plus_upgrade_zeros_counter(self, client, tmp_db_path):
        """Verify-receipt Free→Plus zeros project_chat_used_this_period."""
        from app.models.tier import load_tier_config

        _insert_user(tmp_db_path, user_id="upgrader", tier="free", monthly_limit=0.35)
        # Burn some quota first to confirm zeroing actually happens
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            "UPDATE users SET project_chat_used_this_period = 1, project_chat_period = '2026-04' WHERE id = ?",
            ("upgrader",),
        )
        conn.commit()
        conn.close()

        plus_product = (
            load_tier_config("config/tiers.yml")
            .tiers["plus"]
            .storekit_product_id
        )
        headers = {"Authorization": f"Bearer {_jwt_token('upgrader')}"}
        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": plus_product,
                "transaction_id": "txn_upgrade",
                "is_trial": False,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(tmp_db_path)
        used = conn.execute(
            "SELECT project_chat_used_this_period FROM users WHERE id = ?",
            ("upgrader",),
        ).fetchone()[0]
        conn.close()
        assert used == 0
