"""Cert pin manifest signing service.

iOS pins TLS certificates on `cz.shouldersurf.com`. When Let's Encrypt
rotates the chain (roughly every 90 days, sometimes structurally), the
old pins fail and every iOS call hangs at the TLS handshake. Forcing an
App Store release every quarter just to refresh pins is fragile.

This module signs a small JSON manifest containing the current SPKI
hash list, a monotonic version, and an expiry. iOS fetches the manifest
from `/v1/config/cert-pins`, verifies the signature against a baked-in
Ed25519 public key, and uses the resulting pins. The fetch itself goes
over normal HTTPS (no pin check on the fetch — that would be circular);
trust comes from the signature, not the transport. An attacker on the
fetch can mangle the payload but cannot forge a valid signature without
the private key, so the app rejects any mismatching payload.

The proposal SS sent us is at
`/Users/scottguida/ShoulderSurf/docs/CERT_PINNING_PROPOSAL.md`.

Key custody (also documented in app/config.py):
    - Private key NEVER in this repo.
    - Generated locally on a trusted operator machine with
      `openssl genpkey -algorithm Ed25519`.
    - Stored in GCP Secret Manager as `cert-pin-signing-key-raw-b64`.
    - .env mirror for local dev; .env is gitignored and a defensive
      pattern in .gitignore matches `*signing_private*` files.
    - Loaded at process start via env var `CZ_CERT_PIN_SIGNING_KEY_RAW_B64`
      (32-byte raw Ed25519 private key, base64 encoded). Settings holds it
      as a string; this module decodes it on demand.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import Settings

logger = logging.getLogger("ghostpour.cert_pin_signing")


class CertPinSigningError(RuntimeError):
    """Raised when signing is requested but the key isn't configured or is
    malformed. The public read path tolerates this (returns 503/404);
    the admin publish path surfaces it to the operator."""


def _load_private_key(settings: Settings) -> Ed25519PrivateKey:
    """Decode the configured base64 key into a usable Ed25519 private key."""
    raw_b64 = (settings.cert_pin_signing_key_raw_b64 or "").strip()
    if not raw_b64:
        raise CertPinSigningError(
            "CZ_CERT_PIN_SIGNING_KEY_RAW_B64 is not set; cannot sign cert pin manifest"
        )
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (ValueError, base64.binascii.Error) as e:
        raise CertPinSigningError(f"signing key is not valid base64: {e}")
    if len(raw) != 32:
        raise CertPinSigningError(
            f"signing key must be 32 raw bytes, got {len(raw)}"
        )
    return Ed25519PrivateKey.from_private_bytes(raw)


def get_public_key_b64(settings: Settings) -> Optional[str]:
    """Return the matching public key (raw bytes, base64). This is what
    iOS bakes into the app binary. Safe to expose on the admin endpoint
    so the operator can hand it to SS. Returns None when signing isn't
    configured."""
    try:
        priv = _load_private_key(settings)
    except CertPinSigningError:
        return None
    pub: Ed25519PublicKey = priv.public_key()
    raw_pub = pub.public_bytes_raw()
    return base64.b64encode(raw_pub).decode("ascii")


# --- Canonical payload + signing -------------------------------------------


def _canonical_payload(
    *,
    version: int,
    pins: list[str],
    issued_at_iso: str,
    expires_at_iso: str,
) -> bytes:
    """Bytes that the signature covers. Stable across language clients
    because we control all the fields and serialize them with sorted
    keys + no insignificant whitespace.

    iOS verifies the same canonical form before checking the signature.
    Changing this shape is a breaking change for already-deployed clients.
    """
    payload = {
        "version": int(version),
        "pins": list(pins),
        "issued_at": issued_at_iso,
        "expires_at": expires_at_iso,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_manifest(
    settings: Settings,
    *,
    pins: list[str],
    version: int,
    issued_at: datetime,
    expires_at: datetime,
) -> dict:
    """Produce the wire-shape manifest dict that gets served at
    /v1/config/cert-pins and stored in the cert_pin_manifest table.

    All timestamps in the manifest are RFC 3339 / ISO 8601 UTC strings.
    """
    issued_iso = issued_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    expires_iso = expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    canonical = _canonical_payload(
        version=version, pins=pins,
        issued_at_iso=issued_iso, expires_at_iso=expires_iso,
    )
    priv = _load_private_key(settings)
    sig = priv.sign(canonical)
    sig_b64 = base64.b64encode(sig).decode("ascii")

    return {
        "version": version,
        "pins": list(pins),
        "issued_at": issued_iso,
        "expires_at": expires_iso,
        "signature": sig_b64,
        "algorithm": "ed25519",
    }


def verify_signature(public_key_b64: str, manifest: dict) -> bool:
    """Round-trip verifier — given a base64 raw public key and a manifest
    dict in the wire shape, return True iff the signature checks out.
    Mainly used by tests and by the admin sanity-check endpoint.
    """
    try:
        raw_pub = base64.b64decode(public_key_b64, validate=True)
        pub = Ed25519PublicKey.from_public_bytes(raw_pub)
        sig = base64.b64decode(manifest["signature"], validate=True)
    except Exception as e:
        logger.warning("cert pin verify: malformed inputs: %s", e)
        return False
    canonical = _canonical_payload(
        version=manifest["version"],
        pins=manifest["pins"],
        issued_at_iso=manifest["issued_at"],
        expires_at_iso=manifest["expires_at"],
    )
    try:
        pub.verify(sig, canonical)
        return True
    except InvalidSignature:
        return False


# --- DB read + publish helpers ---------------------------------------------


async def _next_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("SELECT COALESCE(MAX(version), 0) FROM cert_pin_manifest")
    row = await cur.fetchone()
    return int(row[0] if row else 0) + 1


async def latest_manifest(db: aiosqlite.Connection) -> Optional[dict]:
    """Return the wire-shape manifest dict for the highest-version row,
    or None if the table is empty.
    """
    cur = await db.execute(
        """SELECT version, pins_json, issued_at, expires_at, signature
           FROM cert_pin_manifest ORDER BY version DESC LIMIT 1"""
    )
    row = await cur.fetchone()
    if not row:
        return None
    return {
        "version": row[0],
        "pins": json.loads(row[1]),
        "issued_at": row[2],
        "expires_at": row[3],
        "signature": row[4],
        "algorithm": "ed25519",
    }


async def publish_manifest(
    db: aiosqlite.Connection,
    settings: Settings,
    *,
    pins: list[str],
    days_valid: int = 60,
    admin_key_suffix: str | None = None,
) -> dict:
    """Sign a fresh manifest covering `pins` valid for `days_valid` days,
    persist it (monotonic version), and return the wire-shape dict.
    """
    if not pins:
        raise CertPinSigningError("pins list cannot be empty")
    if days_valid <= 0 or days_valid > 365:
        raise CertPinSigningError("days_valid must be between 1 and 365")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days_valid)
    version = await _next_version(db)

    manifest = sign_manifest(
        settings, pins=pins, version=version,
        issued_at=now, expires_at=expires,
    )

    await db.execute(
        """INSERT INTO cert_pin_manifest
           (version, pins_json, issued_at, expires_at, signature, created_at, created_by_admin_key_suffix)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            version,
            json.dumps(manifest["pins"]),
            manifest["issued_at"],
            manifest["expires_at"],
            manifest["signature"],
            now.isoformat(),
            admin_key_suffix,
        ),
    )
    await db.commit()
    logger.info(
        "cert_pin_manifest published version=%d pins=%d expires=%s",
        version, len(pins), manifest["expires_at"],
    )
    return manifest
