"""End-to-end integration tests for subscription endpoints."""

import sqlite3

from tests.conftest import _insert_user, _jwt_token, chat_request


class TestVerifyReceipt:
    def test_verify_receipt_upgrades_tier(self, client, tmp_db_path):
        """Verify receipt with a standard product ID → tier upgraded."""
        _insert_user(tmp_db_path, user_id="upgrade-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('upgrade-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": "com.example.myapp.sub.standard.monthly",
                "transaction_id": "txn_123",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_tier"] == "standard"
        assert data["old_tier"] == "free"
        assert data["is_trial"] is False

    def test_verify_receipt_trial(self, client, tmp_db_path):
        """Trial offer → is_trial=True, trial_end set."""
        _insert_user(tmp_db_path, user_id="trial-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('trial-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": "com.example.myapp.sub.pro.monthly",
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
        _insert_user(tmp_db_path, user_id="downgrade-user", tier="standard", monthly_limit=1.25)
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
