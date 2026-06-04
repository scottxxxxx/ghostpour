"""Provider health daemon tests.

Pins:
- Each provider's classify path (200 healthy, 401 auth fail, 402 budget, transient)
- OpenRouter low-balance proactive alert
- No alert on 429 / 5xx / network transient
- No alert when provider key is unset (skip + healthy=False / detail explains)
- Alert dedup subjects per failure mode
- tick() fires alerts with the right (category, subject)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.services import provider_health as ph


def _settings(
    *, ant="ant-key", oro="oro-key", oai="", thresh=1.00,
) -> Settings:
    return Settings(
        jwt_secret="test-secret-key-that-is-long-enough-for-hs256-validation",
        anthropic_api_key=ant,
        openrouter_api_key=oro,
        openai_api_key=oai,
        openrouter_low_balance_threshold_usd=thresh,
    )


def _fake_response(status: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=json_body if json_body is not None else None,
        text=text if text else None,
    )


# --- classify path --------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_healthy_on_200():
    client = AsyncMock()
    client.post.return_value = _fake_response(200, {"input_tokens": 1})
    r = await ph.check_anthropic("ant-key", client)
    assert r.healthy is True
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_anthropic_auth_failed_on_401():
    client = AsyncMock()
    client.post.return_value = _fake_response(401, text="invalid api key")
    r = await ph.check_anthropic("ant-key", client)
    assert r.healthy is False
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_anthropic_no_key_returns_unhealthy_explanation():
    client = AsyncMock()
    r = await ph.check_anthropic("", client)
    assert r.healthy is False
    assert r.status_code is None
    assert "no API key" in r.detail
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_anthropic_network_error_returns_unhealthy_transient():
    client = AsyncMock()
    client.post.side_effect = httpx.TimeoutException("slow")
    r = await ph.check_anthropic("ant-key", client)
    assert r.healthy is False
    assert r.status_code is None
    assert "network/timeout" in r.detail


# --- OpenRouter -----------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_healthy_when_remaining_above_threshold():
    client = AsyncMock()
    client.get.return_value = _fake_response(
        200, {"data": {"usage": 2.50, "limit": 10.00}},
    )
    r = await ph.check_openrouter("oro-key", low_balance_threshold_usd=1.00, client=client)
    assert r.healthy is True
    assert r.extras["remaining_usd"] == 7.5


@pytest.mark.asyncio
async def test_openrouter_unhealthy_below_threshold():
    client = AsyncMock()
    client.get.return_value = _fake_response(
        200, {"data": {"usage": 9.95, "limit": 10.00}},
    )
    r = await ph.check_openrouter("oro-key", low_balance_threshold_usd=1.00, client=client)
    assert r.healthy is False
    assert r.status_code == 200  # the 200/balance path
    assert r.extras["remaining_usd"] == pytest.approx(0.05, abs=1e-6)


@pytest.mark.asyncio
async def test_openrouter_unlimited_limit_is_healthy():
    client = AsyncMock()
    client.get.return_value = _fake_response(
        200, {"data": {"usage": 12.50, "limit": None}},
    )
    r = await ph.check_openrouter("oro-key", low_balance_threshold_usd=1.00, client=client)
    assert r.healthy is True
    assert r.extras["remaining_usd"] is None


@pytest.mark.asyncio
async def test_openrouter_401_classified_as_auth_fail():
    client = AsyncMock()
    client.get.return_value = _fake_response(401, text="bad key")
    r = await ph.check_openrouter("oro-key", low_balance_threshold_usd=1.00, client=client)
    assert r.healthy is False
    assert r.status_code == 401


# --- OpenAI ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_unset_key_skipped_healthy():
    client = AsyncMock()
    r = await ph.check_openai("", client)
    assert r.healthy is True
    assert r.detail.startswith("no API key")
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_openai_402_classified_as_budget():
    client = AsyncMock()
    client.post.return_value = _fake_response(402, text="quota")
    r = await ph.check_openai("oai-key", client)
    assert r.healthy is False
    assert r.status_code == 402


# --- Alert decision -------------------------------------------------------


def test_alert_decision_healthy_returns_none():
    r = ph.ProbeResult("anthropic", datetime.now(timezone.utc), True, 200, "ok")
    assert ph._alert_decision(r) is None


def test_alert_decision_401_fires_auth_failed():
    r = ph.ProbeResult("anthropic", datetime.now(timezone.utc), False, 401, "x")
    assert ph._alert_decision(r) == ("provider_auth_failed", "anthropic_auth_401")


def test_alert_decision_403_fires_auth_failed():
    r = ph.ProbeResult("openai", datetime.now(timezone.utc), False, 403, "x")
    assert ph._alert_decision(r) == ("provider_auth_failed", "openai_auth_403")


def test_alert_decision_402_fires_budget_exhausted():
    r = ph.ProbeResult("openai", datetime.now(timezone.utc), False, 402, "x")
    assert ph._alert_decision(r) == ("provider_budget_exhausted", "openai_budget_402")


def test_alert_decision_openrouter_low_balance_fires_budget():
    r = ph.ProbeResult(
        "openrouter", datetime.now(timezone.utc), False, 200,
        "remaining=$0.05 below threshold",
        extras={"remaining_usd": 0.05},
    )
    assert ph._alert_decision(r) == ("provider_budget_exhausted", "openrouter_low_balance")


def test_alert_decision_429_does_not_alert():
    r = ph.ProbeResult("anthropic", datetime.now(timezone.utc), False, 429, "rate limit")
    assert ph._alert_decision(r) is None


def test_alert_decision_5xx_does_not_alert():
    r = ph.ProbeResult("anthropic", datetime.now(timezone.utc), False, 503, "down")
    assert ph._alert_decision(r) is None


def test_alert_decision_network_error_does_not_alert():
    r = ph.ProbeResult("anthropic", datetime.now(timezone.utc), False, None, "timeout")
    assert ph._alert_decision(r) is None


# --- tick() end-to-end ----------------------------------------------------


@pytest.mark.asyncio
async def test_tick_fires_no_alerts_when_all_healthy(tmp_path):
    """Happy path: all three providers green, no incidents fired."""
    import aiosqlite
    from app.database import init_db
    db_path = str(tmp_path / "test.db")
    await init_db(f"sqlite+aiosqlite:///{db_path}")

    called = []

    async def _stub(*args, **kwargs):
        called.append(kwargs.get("category"))
        class _R: incident_id="t"; is_new=True; emailed_to=[]; suppressed_reason=None
        return _R()

    with patch.object(
        ph, "check_anthropic",
        new=AsyncMock(return_value=ph.ProbeResult("anthropic", ph._now(), True, 200, "ok")),
    ), patch.object(
        ph, "check_openrouter",
        new=AsyncMock(return_value=ph.ProbeResult(
            "openrouter", ph._now(), True, 200, "remaining=$5.00",
            extras={"remaining_usd": 5.0, "usage_usd": 1.0, "limit_usd": 6.0},
        )),
    ), patch.object(
        ph, "check_openai",
        new=AsyncMock(return_value=ph.ProbeResult(
            "openai", ph._now(), True, None, "no API key configured; skipping probe",
        )),
    ), patch("app.services.alerting.report_incident", new=_stub):
        async with aiosqlite.connect(db_path) as db:
            results = await ph.tick(db, _settings())

    assert all(r.healthy for r in results.values())
    assert called == []  # no incidents fired


@pytest.mark.asyncio
async def test_tick_fires_auth_failed_on_anthropic_401(tmp_path):
    import aiosqlite
    from app.database import init_db
    db_path = str(tmp_path / "test.db")
    await init_db(f"sqlite+aiosqlite:///{db_path}")

    captured = {}

    async def _stub(*args, **kwargs):
        captured["category"] = kwargs.get("category")
        captured["subject"] = kwargs.get("subject")
        class _R: incident_id="t"; is_new=True; emailed_to=[]; suppressed_reason=None
        return _R()

    with patch.object(
        ph, "check_anthropic",
        new=AsyncMock(return_value=ph.ProbeResult("anthropic", ph._now(), False, 401, "bad")),
    ), patch.object(
        ph, "check_openrouter",
        new=AsyncMock(return_value=ph.ProbeResult(
            "openrouter", ph._now(), True, 200, "ok",
            extras={"remaining_usd": 5.0, "usage_usd": 1.0, "limit_usd": 6.0},
        )),
    ), patch.object(
        ph, "check_openai",
        new=AsyncMock(return_value=ph.ProbeResult(
            "openai", ph._now(), True, None, "skip",
        )),
    ), patch("app.services.alerting.report_incident", new=_stub):
        async with aiosqlite.connect(db_path) as db:
            await ph.tick(db, _settings())

    assert captured["category"] == "provider_auth_failed"
    assert captured["subject"] == "anthropic_auth_401"


@pytest.mark.asyncio
async def test_tick_updates_module_last_check_cache(tmp_path):
    """The status endpoint reads from this cache."""
    import aiosqlite
    from app.database import init_db
    db_path = str(tmp_path / "test.db")
    await init_db(f"sqlite+aiosqlite:///{db_path}")

    ph._last_check.clear()

    with patch.object(
        ph, "check_anthropic",
        new=AsyncMock(return_value=ph.ProbeResult("anthropic", ph._now(), True, 200, "ok")),
    ), patch.object(
        ph, "check_openrouter",
        new=AsyncMock(return_value=ph.ProbeResult(
            "openrouter", ph._now(), True, 200, "ok",
            extras={"remaining_usd": 5.0, "usage_usd": 1.0, "limit_usd": 6.0},
        )),
    ), patch.object(
        ph, "check_openai",
        new=AsyncMock(return_value=ph.ProbeResult(
            "openai", ph._now(), True, None, "skip",
        )),
    ):
        async with aiosqlite.connect(db_path) as db:
            await ph.tick(db, _settings())

    cached = ph.get_last_check()
    assert set(cached.keys()) == {"anthropic", "openrouter", "openai"}
    assert all(isinstance(r, ph.ProbeResult) for r in cached.values())


# --- Admin status endpoint ------------------------------------------------


def test_admin_status_endpoint_returns_empty_before_first_tick(client):
    """Before the daemon runs once, the cache is empty — endpoint
    returns providers: {} rather than crashing."""
    ph._last_check.clear()
    resp = client.get(
        "/webhooks/admin/provider-health/status",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"] == {}
    assert body["interval_seconds"] == 900


def test_admin_status_endpoint_returns_cached_results(client):
    ph._last_check.clear()
    ph._last_check["anthropic"] = ph.ProbeResult(
        "anthropic", datetime(2026, 6, 4, tzinfo=timezone.utc),
        True, 200, "ok",
    )
    resp = client.get(
        "/webhooks/admin/provider-health/status",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "anthropic" in body["providers"]
    assert body["providers"]["anthropic"]["healthy"] is True
    assert body["providers"]["anthropic"]["status_code"] == 200


def test_admin_status_requires_admin_key(client):
    resp = client.get(
        "/webhooks/admin/provider-health/status",
        headers={"X-Admin-Key": "wrong"},
    )
    assert resp.status_code == 403
