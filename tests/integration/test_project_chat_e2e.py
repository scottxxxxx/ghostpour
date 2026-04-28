"""Integration tests for the Project Chat policy endpoint and /v1/chat path."""

import sqlite3

from tests.conftest import _insert_user, _jwt_token


PREFLIGHT_PATH = "/v1/features/project-chat/check"


class TestProjectChatPreflight:
    def test_unauthenticated_returns_login_required(self, client):
        """No JWT → verdict=login_required, regardless of selected_model."""
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Default flag is "plus" — non-logged-in always gets login_required
        assert body["verdict"] == "login_required"
        assert body["cta"]["kind"] == "login_required"

    def test_plus_user_external_model_routes_to_user_model(self, client, tmp_db_path):
        """Plus user with external model selected → send_to_user_model under default 'plus' policy."""
        _insert_user(tmp_db_path, user_id="plus-pc", tier="plus", monthly_limit=-1)
        headers = {"Authorization": f"Bearer {_jwt_token('plus-pc')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "send_to_user_model"

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

    def test_free_user_with_quota_returns_send_to_gp_with_cta(self, client, tmp_db_path):
        """Free user, quota=1, no use yet → send_to_gp_with_cta + quota_remaining CTA."""
        _insert_user(tmp_db_path, user_id="free-pc", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('free-pc')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp_with_cta"
        assert body["cta"]["kind"] == "quota_remaining"
        assert body["quota_remaining"] == 1
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
