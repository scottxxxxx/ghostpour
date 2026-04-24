"""End-to-end integration tests for subscription endpoints."""

import sqlite3

from app.models.tier import load_tier_config
from tests.conftest import _insert_user, _jwt_token, chat_request

# Read product IDs from tier config (respects product-ids.yml overrides)
_tier_config = load_tier_config("config/tiers.yml")
_PLUS_PRODUCT = _tier_config.tiers["plus"].storekit_product_id
_PRO_PRODUCT = _tier_config.tiers["pro"].storekit_product_id


class TestVerifyReceipt:
    def test_verify_receipt_upgrades_tier(self, client, tmp_db_path):
        """Verify receipt with a standard product ID → tier upgraded."""
        _insert_user(tmp_db_path, user_id="upgrade-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('upgrade-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_123",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_tier"] == "plus"
        assert data["old_tier"] == "free"
        assert data["is_trial"] is False

    def test_verify_receipt_trial(self, client, tmp_db_path):
        """Trial offer → is_trial=True, trial_end set."""
        _insert_user(tmp_db_path, user_id="trial-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('trial-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PRO_PRODUCT,
                "transaction_id": "txn_456",
                "offer_type": "introductory",
                "offer_price": 0,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_trial"] is True
        assert "trial_end" in data

    def test_verify_receipt_idempotent_preserves_usage(self, client, tmp_db_path):
        """Re-verification of same tier should NOT reset monthly_used_usd.

        SS calls verify-receipt on every launch. If GP resets allocation each
        time, users lose their accumulated usage and the hours.used display
        shows 0 even when they've consumed real quota.
        """
        _insert_user(
            tmp_db_path,
            user_id="idempotent-user",
            tier="plus",
            monthly_limit=2.40,
            monthly_used=0.50,
        )
        headers = {"Authorization": f"Bearer {_jwt_token('idempotent-user')}"}

        # Re-verify same subscription (not a tier change)
        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_same",
                "is_trial": False,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        # monthly_used_usd should be preserved (not reset to 0)
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT monthly_used_usd, monthly_cost_limit_usd FROM users WHERE id = ?",
            ("idempotent-user",),
        ).fetchone()
        conn.close()
        assert row[0] == 0.50, f"monthly_used_usd was reset to {row[0]}, expected 0.50"
        assert row[1] == 2.40

    def test_verify_receipt_idempotent_trial_preserves_usage(self, client, tmp_db_path):
        """Trial re-verification should NOT reset monthly_used_usd either."""
        _insert_user(
            tmp_db_path,
            user_id="idempotent-trial-user",
            tier="plus",
            monthly_limit=0.50,
            monthly_used=0.30,
            is_trial=True,
        )
        headers = {"Authorization": f"Bearer {_jwt_token('idempotent-trial-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_trial_same",
                "offer_type": "introductory",
                "offer_price": 0,
                "is_trial": True,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT monthly_used_usd, is_trial FROM users WHERE id = ?",
            ("idempotent-trial-user",),
        ).fetchone()
        conn.close()
        assert row[0] == 0.30, f"trial monthly_used_usd was reset to {row[0]}, expected 0.30"
        assert row[1] == 1  # still in trial

    def test_verify_receipt_unknown_product(self, client, tmp_db_path):
        """Unknown product ID → 400."""
        _insert_user(tmp_db_path, user_id="unknown-product-user", tier="free")
        headers = {"Authorization": f"Bearer {_jwt_token('unknown-product-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": "com.fake.product",
                "transaction_id": "txn_789",
            },
            headers=headers,
        )
        assert resp.status_code == 400


class TestSyncSubscription:
    def test_sync_downgrade_to_free(self, client, tmp_db_path):
        """No active product → downgrade to free."""
        _insert_user(tmp_db_path, user_id="downgrade-user", tier="plus", monthly_limit=2.40)
        headers = {"Authorization": f"Bearer {_jwt_token('downgrade-user')}"}

        resp = client.post(
            "/v1/sync-subscription",
            json={"active_product_id": None},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "downgraded"
        assert data["new_tier"] == "free"

    def test_sync_no_change(self, client, tmp_db_path):
        """Already on correct tier → no change."""
        _insert_user(tmp_db_path, user_id="synced-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('synced-user')}"}

        resp = client.post(
            "/v1/sync-subscription",
            json={"active_product_id": None},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "none"


class TestUsageMe:
    def test_usage_me_response_shape(self, client, free_user):
        """GET /v1/usage/me returns expected fields."""
        resp = client.get("/v1/usage/me", headers=free_user["headers"])
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "tier" in data
        assert "allocation" in data
        assert "monthly_limit_usd" in data["allocation"]
        assert "monthly_used_usd" in data["allocation"]
        assert "percent_used" in data["allocation"]
        assert "hours" in data
        assert "this_month" in data
        assert "features" in data
        assert "summary_mode" in data

    def test_usage_me_reflects_tier(self, client, free_user, pro_user):
        """Different tiers return different allocation limits."""
        free_resp = client.get("/v1/usage/me", headers=free_user["headers"])
        pro_resp = client.get("/v1/usage/me", headers=pro_user["headers"])

        free_limit = free_resp.json()["allocation"]["monthly_limit_usd"]
        pro_limit = pro_resp.json()["allocation"]["monthly_limit_usd"]
        assert pro_limit > free_limit
