"""Subscription offer-code minting (App Store Connect API, JWT-signed).

GP owns minting offer codes (Scott 2026-06-27): the app only presents Apple's
redeem sheet; GP generates the codes programmatically. "Minting" = generating
one-time-use code STRINGS against an offer that already exists. The offer itself
(the discount, eligibility, duration) is configured ONCE by hand in App Store
Connect — Apple has no API to create the offer config, only to mint codes for it.

DISTINCT from app_store_server_api.py: that client uses an In-App Purchase key to
PULL subscription state from the Server API. This one uses an App Store CONNECT
API key (team key, Admin/App Manager role) to call the Connect API and mint codes.
The two keys are different types and not interchangeable.

DORMANT by default: `is_configured()` is False until the Connect issuer id / key
id / .p8 are provisioned, so nothing here runs or fails on an un-provisioned deploy.

Flow:
  1. POST /v1/subscriptionOfferCodeOneTimeUseCodes  (batch: numberOfCodes 10-10000,
     expirationDate ISO-8601 date, active) related to a subscriptionOfferCodes id
     → returns the created batch resource id.
  2. GET  /v1/subscriptionOfferCodeOneTimeUseCodes/{id}/values  → CSV of the actual
     redeemable code strings.

Docs: https://developer.apple.com/documentation/appstoreconnectapi/subscription-offer-codes
"""

from __future__ import annotations

import base64
import logging
import time

import httpx
import jwt

from app.config import get_settings

logger = logging.getLogger("ghostpour.offer_codes")

_BASE = "https://api.appstoreconnect.apple.com"

# Apple's batch bounds for one-time-use codes.
MIN_CODES = 10
MAX_CODES = 10000


class OfferCodeError(Exception):
    """Minting failed (not configured, Apple rejected, or values unavailable)."""


def is_configured() -> bool:
    s = get_settings()
    return bool(
        s.asc_connect_issuer_id
        and s.asc_connect_key_id
        and s.asc_connect_private_key_b64
    )


def _private_key_pem() -> bytes:
    """The .p8 is stored base64-encoded; decode to the PEM bytes PyJWT wants."""
    return base64.b64decode(get_settings().asc_connect_private_key_b64.strip())


def _signed_jwt() -> str:
    """ES256 bearer token for the App Store Connect API (valid ~20 min).

    Unlike the Server API token there is NO `bid` claim — the Connect API is
    account-scoped, not bundle-scoped. A team key needs no `scope`.
    """
    s = get_settings()
    now = int(time.time())
    headers = {"alg": "ES256", "kid": s.asc_connect_key_id, "typ": "JWT"}
    payload = {
        "iss": s.asc_connect_issuer_id,
        "iat": now,
        "exp": now + 1200,
        "aud": "appstoreconnect-v1",
    }
    return jwt.encode(payload, _private_key_pem(), algorithm="ES256", headers=headers)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_signed_jwt()}"}


async def mint_one_time_use_codes(
    offer_code_id: str, number_of_codes: int, expiration_date: str
) -> str:
    """Create a batch of one-time-use codes for an existing offer.

    Args:
      offer_code_id: id of the configured `subscriptionOfferCodes` resource.
      number_of_codes: 10..10000 (Apple's bounds).
      expiration_date: ISO-8601 date "YYYY-MM-DD" (codes expire 12:00am PT that
        day; sandbox max 6 months out).

    Returns the created batch resource id (pass to fetch_code_values).
    Raises OfferCodeError on misconfig or a non-2xx Apple response.
    """
    if not is_configured():
        raise OfferCodeError("App Store Connect API key not provisioned")
    if not (MIN_CODES <= number_of_codes <= MAX_CODES):
        raise OfferCodeError(
            f"number_of_codes must be {MIN_CODES}-{MAX_CODES} (got {number_of_codes})"
        )
    body = {
        "data": {
            "type": "subscriptionOfferCodeOneTimeUseCodes",
            "attributes": {
                "numberOfCodes": number_of_codes,
                "expirationDate": expiration_date,
                "active": True,
            },
            "relationships": {
                "offerCode": {
                    "data": {"type": "subscriptionOfferCodes", "id": offer_code_id}
                }
            },
        }
    }
    url = f"{_BASE}/v1/subscriptionOfferCodeOneTimeUseCodes"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=_auth_headers())
    except Exception as e:
        raise OfferCodeError(f"Connect API request failed: {e}") from e
    if resp.status_code not in (200, 201):
        raise OfferCodeError(f"Apple rejected mint ({resp.status_code}): {resp.text[:300]}")
    batch_id = (resp.json().get("data") or {}).get("id")
    if not batch_id:
        raise OfferCodeError("mint response missing data.id")
    return batch_id


async def fetch_code_values(batch_id: str) -> list[str]:
    """Download the actual redeemable code strings for a minted batch.

    Apple returns the values as CSV; the codes are the non-empty lines after an
    optional header row. Raises OfferCodeError on a non-2xx response.
    """
    if not is_configured():
        raise OfferCodeError("App Store Connect API key not provisioned")
    url = f"{_BASE}/v1/subscriptionOfferCodeOneTimeUseCodes/{batch_id}/values"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=_auth_headers())
    except Exception as e:
        raise OfferCodeError(f"Connect API request failed: {e}") from e
    if resp.status_code != 200:
        raise OfferCodeError(f"Apple values fetch failed ({resp.status_code}): {resp.text[:300]}")
    return _parse_codes_csv(resp.text)


def _parse_codes_csv(text: str) -> list[str]:
    """Pull code strings from Apple's CSV. Drops a leading 'Code' header and any
    blank lines; codes are single-column alphanumeric tokens."""
    codes: list[str] = []
    for line in text.splitlines():
        tok = line.strip().strip('"')
        if not tok or tok.lower() == "code":
            continue
        codes.append(tok)
    return codes


async def mint_and_fetch(
    offer_code_id: str, number_of_codes: int, expiration_date: str
) -> dict:
    """Convenience for the admin path: mint a batch and return its codes.

    Returns {"batch_id": str, "codes": [str, ...], "count": int}.
    """
    batch_id = await mint_one_time_use_codes(offer_code_id, number_of_codes, expiration_date)
    codes = await fetch_code_values(batch_id)
    return {"batch_id": batch_id, "codes": codes, "count": len(codes)}
