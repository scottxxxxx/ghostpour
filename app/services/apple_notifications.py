"""Apple App Store Server Notifications V2 — JWS verification and decoding.

Apple sends a signed JWS payload to our webhook. The JWS header contains an
x5c certificate chain (leaf → intermediate → root). We verify:
  1. The root cert in x5c matches Apple Root CA - G3
  2. Each cert in the chain was signed by the next
  3. The JWS signature is valid against the leaf cert's public key
  4. The payload is decoded and returned

Nested JWS fields (signedTransactionInfo, signedRenewalInfo) are verified
and decoded the same way.

See: https://developer.apple.com/documentation/appstoreservernotifications
"""

import base64
import json
import logging
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

logger = logging.getLogger("ghostpour.apple_notifications")

# Apple Root CA - G3, shipped with the image
_ROOT_CA_PATH = Path(__file__).parent.parent.parent / "config" / "AppleRootCA-G3.pem"
_apple_root_ca: x509.Certificate | None = None


def _get_apple_root_ca() -> x509.Certificate:
    """Load and cache the Apple Root CA - G3 certificate."""
    global _apple_root_ca
    if _apple_root_ca is None:
        _apple_root_ca = x509.load_pem_x509_certificate(_ROOT_CA_PATH.read_bytes())
    return _apple_root_ca


class AppleJWSError(Exception):
    """Raised when JWS verification fails."""


def _b64url_decode(data: str) -> bytes:
    """Decode base64url without padding."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def _verify_x5c_chain(x5c: list[str], bundle_id: str) -> x509.Certificate:
    """Verify the x5c certificate chain and return the leaf certificate.

    Validates:
    - Chain has at least 3 certificates (leaf, intermediate, root)
    - Root certificate matches Apple Root CA - G3
    - Each certificate is signed by the next in the chain
    """
    if len(x5c) < 3:
        raise AppleJWSError(f"x5c chain too short: {len(x5c)} certs (need >= 3)")

    # Parse all certs
    certs = []
    for i, cert_b64 in enumerate(x5c):
        try:
            cert_der = base64.b64decode(cert_b64)
            cert = x509.load_der_x509_certificate(cert_der)
            certs.append(cert)
        except Exception as e:
            raise AppleJWSError(f"Failed to parse x5c cert [{i}]: {e}")

    # Verify root matches Apple Root CA - G3
    apple_root = _get_apple_root_ca()
    root_cert = certs[-1]
    if root_cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ) != apple_root.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ):
        raise AppleJWSError("Root certificate does not match Apple Root CA - G3")

    # Verify chain: each cert[i] should be signed by cert[i+1]
    for i in range(len(certs) - 1):
        try:
            issuer_public_key = certs[i + 1].public_key()
            issuer_public_key.verify(
                certs[i].signature,
                certs[i].tbs_certificate_bytes,
                ec.ECDSA(certs[i].signature_hash_algorithm),
            )
        except InvalidSignature:
            raise AppleJWSError(f"Certificate chain broken at index {i}")
        except Exception as e:
            raise AppleJWSError(f"Chain verification error at index {i}: {e}")

    return certs[0]  # leaf


def decode_and_verify_jws(signed_payload: str, bundle_id: str) -> dict:
    """Decode and verify an Apple JWS payload.

    Returns the decoded JSON payload.
    Raises AppleJWSError on verification failure.
    """
    parts = signed_payload.split(".")
    if len(parts) != 3:
        raise AppleJWSError(f"Invalid JWS format: expected 3 parts, got {len(parts)}")

    header_b64, payload_b64, signature_b64 = parts

    # Decode header to get x5c chain
    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception as e:
        raise AppleJWSError(f"Failed to decode JWS header: {e}")

    if header.get("alg") != "ES256":
        raise AppleJWSError(f"Unexpected algorithm: {header.get('alg')}")

    x5c = header.get("x5c")
    if not x5c or not isinstance(x5c, list):
        raise AppleJWSError("Missing x5c certificate chain in JWS header")

    # Verify certificate chain
    leaf_cert = _verify_x5c_chain(x5c, bundle_id)

    # Verify JWS signature
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = _b64url_decode(signature_b64)

    # ES256 signature is r||s (64 bytes), convert to DER for cryptography lib
    if len(signature) == 64:
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        signature = utils.encode_dss_signature(r, s)

    try:
        leaf_cert.public_key().verify(
            signature,
            signing_input,
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature:
        raise AppleJWSError("JWS signature verification failed")

    # Decode payload
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as e:
        raise AppleJWSError(f"Failed to decode JWS payload: {e}")

    return payload


def decode_notification(signed_payload: str, bundle_id: str) -> dict:
    """Decode a full Apple Server Notification V2 payload.

    Verifies the outer JWS and any nested signed fields
    (signedTransactionInfo, signedRenewalInfo).

    Returns the notification dict with nested signed fields decoded inline.
    """
    notification = decode_and_verify_jws(signed_payload, bundle_id)

    # Decode nested signed fields in the data object
    data = notification.get("data", {})
    for field in ("signedTransactionInfo", "signedRenewalInfo"):
        if field in data and isinstance(data[field], str):
            try:
                data[field] = decode_and_verify_jws(data[field], bundle_id)
            except AppleJWSError as e:
                logger.warning("Failed to verify nested %s: %s", field, e)
                # Keep the raw string; caller can decide what to do

    return notification
