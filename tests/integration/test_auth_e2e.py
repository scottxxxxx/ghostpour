"""End-to-end integration tests for auth endpoints."""

from unittest.mock import patch, MagicMock

from tests.conftest import _insert_user, _jwt_token


class TestAppleAuth:
    def test_apple_auth_creates_new_user(self, client):
        """Valid Apple identity token → new user created, tokens returned."""
        mock_claims = {"sub": "apple_sub_new", "email": "new@test.com"}
        with patch(
            "app.services.apple_auth.AppleAuthVerifier.verify_identity_token",
            return_value=mock_claims,
        ):
            resp = client.post(
                "/auth/apple",
                json={"identity_token": "mock.apple.token", "full_name": "Test User"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["tier"] == "free"
        assert data["user"]["email"] == "new@test.com"

    def test_apple_auth_existing_user(self, client, tmp_db_path):
        """Existing user re-authenticates → same user, tokens returned."""
        _insert_user(tmp_db_path, user_id="existing-user", tier="pro")
        mock_claims = {"sub": "apple_sub_existing-user", "email": "updated@test.com"}
        with patch(
            "app.services.apple_auth.AppleAuthVerifier.verify_identity_token",
            return_value=mock_claims,
        ):
            resp = client.post(
                "/auth/apple",
                json={"identity_token": "mock.apple.token"},
            )
        assert resp.status_code == 200
        assert resp.json()["user"]["tier"] == "pro"

    def test_apple_auth_invalid_token(self, client):
        """Invalid Apple token → 401."""
        with patch(
            "app.services.apple_auth.AppleAuthVerifier.verify_identity_token",
            side_effect=ValueError("bad token"),
        ):
            resp = client.post(
                "/auth/apple",
                json={"identity_token": "invalid.token"},
            )
        assert resp.status_code == 401


class TestRefreshToken:
    def test_refresh_token_rotation(self, client):
        """Valid refresh token → old revoked, new pair returned."""
        # First, create a user via Apple auth
        mock_claims = {"sub": "apple_sub_refresh_test", "email": "refresh@test.com"}
        with patch(
            "app.services.apple_auth.AppleAuthVerifier.verify_identity_token",
            return_value=mock_claims,
        ):
            auth_resp = client.post(
                "/auth/apple",
                json={"identity_token": "mock.apple.token"},
            )
        refresh_token = auth_resp.json()["refresh_token"]

        # Use refresh token
        resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["refresh_token"] != refresh_token  # New token issued

        # Old refresh token should be revoked
        resp2 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp2.status_code == 401

    def test_invalid_refresh_token(self, client):
        """Invalid refresh token → 401."""
        resp = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert resp.status_code == 401

    def test_expired_access_token_rejected(self, client, tmp_db_path):
        """Expired JWT on /v1/chat → 401."""
        from app.services.jwt_service import JWTService
        svc = JWTService(
            secret="test-secret-key-that-is-long-enough-for-hs256-validation",
            algorithm="HS256",
            access_expire_minutes=-1,  # Already expired
            refresh_expire_days=30,
        )
        _insert_user(tmp_db_path, user_id="expired-user")
        token = svc.create_access_token("expired-user")
        resp = client.post(
            "/v1/chat",
            json={"provider": "auto", "model": "auto", "system_prompt": "test", "user_content": "test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
