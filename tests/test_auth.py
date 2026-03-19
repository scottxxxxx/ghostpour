"""Unit tests for JWT service."""

import time

import pytest

from app.services.jwt_service import JWTService


@pytest.fixture
def jwt_service():
    return JWTService(
        secret="test-secret-key-for-testing-only",
        algorithm="HS256",
        access_expire_minutes=60,
        refresh_expire_days=30,
    )


def test_create_and_verify_access_token(jwt_service: JWTService):
    token = jwt_service.create_access_token("user-123", "subscriber")
    payload = jwt_service.verify_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["tier"] == "subscriber"
    assert payload["type"] == "access"


def test_expired_token_raises():
    service = JWTService(
        secret="test-secret",
        access_expire_minutes=0,  # Immediate expiry
    )
    token = service.create_access_token("user-123", "free")
    time.sleep(1)
    with pytest.raises(Exception):  # pyjwt.ExpiredSignatureError
        service.verify_access_token(token)


def test_invalid_token_raises(jwt_service: JWTService):
    with pytest.raises(Exception):
        jwt_service.verify_access_token("not.a.valid.token")


def test_refresh_token_hash():
    raw, hashed, expires = JWTService(secret="s").create_refresh_token()
    assert len(raw) == 64
    assert len(hashed) == 64
    assert JWTService.hash_token(raw) == hashed


def test_different_secrets_fail():
    service1 = JWTService(secret="secret-1")
    service2 = JWTService(secret="secret-2")
    token = service1.create_access_token("user-1", "free")
    with pytest.raises(Exception):
        service2.verify_access_token(token)
