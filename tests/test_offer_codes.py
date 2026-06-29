"""Offer-code minting (App Store Connect API): dormant by default, builds the
Connect-API JWT (no bid) and the one-time-use-codes request, parses the CSV."""

from __future__ import annotations

import base64

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services import offer_codes


def _provision(monkeypatch):
    """Generate an EC P-256 key and provision the Connect API settings; return
    the public key for verifying tokens the service signs."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    s = offer_codes.get_settings()
    monkeypatch.setattr(s, "asc_connect_issuer_id", "issuer-uuid", raising=False)
    monkeypatch.setattr(s, "asc_connect_key_id", "KEYID123", raising=False)
    monkeypatch.setattr(
        s, "asc_connect_private_key_b64", base64.b64encode(pem).decode(), raising=False
    )
    return key.public_key()


class _FakeResp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Records the last request and replays canned Apple responses."""
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        return _FakeResp(201, {"data": {"id": "BATCH123"}})

    async def get(self, url, headers=None):
        _FakeClient.captured = {"url": url, "headers": headers}
        return _FakeResp(200, text="Code\nABC-123\nDEF-456\n\n")


def test_is_configured_false_without_keys(client):
    assert offer_codes.is_configured() is False


@pytest.mark.asyncio
async def test_mint_dormant_raises(client):
    with pytest.raises(offer_codes.OfferCodeError):
        await offer_codes.mint_one_time_use_codes("offer1", 10, "2026-12-31")


@pytest.mark.asyncio
async def test_mint_rejects_out_of_bounds(client, monkeypatch):
    _provision(monkeypatch)
    for n in (9, 10001):
        with pytest.raises(offer_codes.OfferCodeError):
            await offer_codes.mint_one_time_use_codes("offer1", n, "2026-12-31")


def test_connect_jwt_has_no_bid_and_right_aud(client, monkeypatch):
    pub = _provision(monkeypatch)
    token = offer_codes._signed_jwt()
    claims = jwt.decode(token, pub, algorithms=["ES256"], audience="appstoreconnect-v1")
    assert claims["iss"] == "issuer-uuid"
    assert claims["aud"] == "appstoreconnect-v1"
    assert "bid" not in claims  # Connect API is account-scoped, not bundle-scoped
    assert jwt.get_unverified_header(token)["kid"] == "KEYID123"


def test_parse_codes_csv_drops_header_and_blanks():
    codes = offer_codes._parse_codes_csv('Code\n"ABC-123"\nDEF-456\n\n')
    assert codes == ["ABC-123", "DEF-456"]


@pytest.mark.asyncio
async def test_mint_and_fetch_builds_request_and_parses_values(client, monkeypatch):
    _provision(monkeypatch)
    monkeypatch.setattr(offer_codes.httpx, "AsyncClient", _FakeClient)
    out = await offer_codes.mint_and_fetch("OFFER42", 10, "2026-12-31")
    assert out == {"batch_id": "BATCH123", "codes": ["ABC-123", "DEF-456"], "count": 2}
    # re-run the mint alone so captured holds the POST body, then assert its shape
    # matches Apple's required create request.
    await offer_codes.mint_one_time_use_codes("OFFER42", 10, "2026-12-31")
    data = _FakeClient.captured["json"]["data"]
    assert data["type"] == "subscriptionOfferCodeOneTimeUseCodes"
    assert data["attributes"]["numberOfCodes"] == 10
    assert data["attributes"]["expirationDate"] == "2026-12-31"
    assert data["attributes"]["active"] is True
    rel = data["relationships"]["offerCode"]["data"]
    assert rel == {"type": "subscriptionOfferCodes", "id": "OFFER42"}


def test_admin_mint_endpoint_dormant_returns_400(client):
    r = client.post(
        "/webhooks/admin/offer-codes/mint",
        json={"offer_code_id": "x", "number_of_codes": 10, "expiration_date": "2026-12-31"},
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert r.status_code == 400
    assert "not provisioned" in r.json()["detail"]
