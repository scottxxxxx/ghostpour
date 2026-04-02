"""Tests for Apple App Store Server Notifications V2."""

import base64
import json

import pytest

from app.services.apple_notifications import (
    AppleJWSError,
    _b64url_decode,
    _verify_x5c_chain,
    decode_and_verify_jws,
)


def test_b64url_decode_no_padding():
    """base64url decoding should handle missing padding."""
    # "hello" in base64url is "aGVsbG8"
    assert _b64url_decode("aGVsbG8") == b"hello"


def test_b64url_decode_with_padding():
    assert _b64url_decode("aGVsbG8=") == b"hello"


def test_invalid_jws_format():
    """JWS with wrong number of parts should raise."""
    with pytest.raises(AppleJWSError, match="expected 3 parts"):
        decode_and_verify_jws("not.a.valid.jws.token", "com.test")


def test_invalid_jws_header():
    """JWS with non-JSON header should raise."""
    bad_header = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
    payload = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    with pytest.raises(AppleJWSError, match="Failed to decode JWS header"):
        decode_and_verify_jws(f"{bad_header}.{payload}.sig", "com.test")


def test_missing_x5c():
    """JWS without x5c chain should raise."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "ES256"}).encode()
    ).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    with pytest.raises(AppleJWSError, match="Missing x5c"):
        decode_and_verify_jws(f"{header}.{payload}.sig", "com.test")


def test_wrong_algorithm():
    """JWS with non-ES256 algorithm should raise."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "x5c": ["a", "b", "c"]}).encode()
    ).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    with pytest.raises(AppleJWSError, match="Unexpected algorithm"):
        decode_and_verify_jws(f"{header}.{payload}.sig", "com.test")


def test_x5c_chain_too_short():
    """x5c chain with fewer than 3 certs should raise."""
    with pytest.raises(AppleJWSError, match="too short"):
        _verify_x5c_chain(["cert1", "cert2"], "com.test")


def test_x5c_chain_invalid_cert():
    """x5c chain with invalid certificate data should raise."""
    with pytest.raises(AppleJWSError, match="Failed to parse"):
        _verify_x5c_chain(["not-a-cert", "not-a-cert", "not-a-cert"], "com.test")
