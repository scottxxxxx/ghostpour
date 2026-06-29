"""App Store Server API client: dormant by default, parses Apple's status shape."""

from __future__ import annotations

import pytest

from app.services import app_store_server_api as assa


def test_is_configured_false_without_keys(client):
    # No issuer/key/.p8 provisioned in tests → client stays dormant.
    assert assa.is_configured() is False


@pytest.mark.asyncio
async def test_get_subscription_state_dormant_returns_none(client):
    # Dormant: must short-circuit to None without any network call.
    assert await assa.get_subscription_state("anything") is None


def test_bid_is_single_bundle_from_comma_list(client, monkeypatch):
    # apple_bundle_id may be a comma-joined list; bid must be one bundle.
    s = assa.get_settings()
    monkeypatch.setattr(s, "app_store_bundle_id", "", raising=False)
    monkeypatch.setattr(s, "apple_bundle_id", "com.a.First,com.b.Second", raising=False)
    assert assa._bid() == "com.a.First"
    # explicit override wins
    monkeypatch.setattr(s, "app_store_bundle_id", "com.c.Explicit", raising=False)
    assert assa._bid() == "com.c.Explicit"


def test_entitled_status_constants():
    # Active (1) and grace (4) are entitled; expired/retry/revoked are not.
    assert assa._STATUS_ACTIVE in assa._ENTITLED_STATUSES
    assert assa._STATUS_GRACE in assa._ENTITLED_STATUSES
    assert assa._STATUS_EXPIRED not in assa._ENTITLED_STATUSES
    assert assa._STATUS_REVOKED not in assa._ENTITLED_STATUSES
