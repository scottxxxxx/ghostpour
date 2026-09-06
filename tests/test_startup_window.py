"""The deploy window: nothing slow may hold the port shut.

uvicorn binds only after lifespan startup returns, so an await in there is
downtime at the edge on every deploy. Measured on prod 2026-09-06: 17.6
seconds of `connect() failed (111: Connection refused)` per deploy, 12.5 of
it inside lifespan, and the pricing fetch is an HTTP GET to a third party
with a 30 second client timeout.
"""
import asyncio
import time

import pytest

from app.services.pricing import (
    EMPTY_PRICES_RETRY_SECONDS, FIRST_FETCH_BUDGET_SECONDS, PricingService,
)


@pytest.mark.asyncio
async def test_a_hanging_upstream_cannot_hold_the_port_shut(monkeypatch):
    """The whole point. A pricing host that never answers must cost us the
    budget and not its own 30 second client timeout."""
    started = asyncio.Event()

    async def _hang(self):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(PricingService, "_fetch", _hang)
    monkeypatch.setattr("app.services.pricing.FIRST_FETCH_BUDGET_SECONDS", 0.2)
    svc = PricingService()
    t0 = time.monotonic()
    await svc.start()
    elapsed = time.monotonic() - t0
    assert started.is_set(), "the fetch must still be attempted, not skipped"
    assert elapsed < 2.0, f"start() blocked {elapsed:.1f}s; the port stays shut that long"
    await svc.stop()


@pytest.mark.asyncio
async def test_a_fast_upstream_is_still_awaited_so_prices_are_warm(monkeypatch):
    """Bounding the wait must not turn every boot into an empty table."""
    async def _quick(self):
        self._prices = {"gpt-5.2": {"input_cost_per_token": 1e-6}}

    monkeypatch.setattr(PricingService, "_fetch", _quick)
    svc = PricingService()
    await svc.start()
    assert svc._prices, "a fetch that returns in time must still land before the port opens"
    await svc.stop()


@pytest.mark.asyncio
async def test_an_abandoned_first_fetch_is_retried_in_a_minute_not_a_day(monkeypatch):
    """The loop used to sleep the refresh interval FIRST, which was only safe
    while start() awaited a complete fetch. With the fetch abandonable, an
    empty table has to be retried soon or we serve 24 hours with no prices."""
    assert EMPTY_PRICES_RETRY_SECONDS <= 300
    assert FIRST_FETCH_BUDGET_SECONDS <= 10

    slept: list[float] = []
    calls = {"n": 0}

    async def _fake_sleep(sec):
        slept.append(sec)
        if len(slept) >= 2:
            raise asyncio.CancelledError

    async def _fetch(self):
        calls["n"] += 1
        self._prices = {"m": {}}     # the loop's own retry succeeds

    monkeypatch.setattr("app.services.pricing.asyncio.sleep", _fake_sleep)
    monkeypatch.setattr(PricingService, "_fetch", _fetch)
    svc = PricingService(refresh_interval=86400)
    with pytest.raises(asyncio.CancelledError):
        await svc._refresh_loop()
    assert slept[0] == EMPTY_PRICES_RETRY_SECONDS, (
        f"empty table waited {slept[0]}s before retrying, not {EMPTY_PRICES_RETRY_SECONDS}")
    assert slept[1] == 86400, "once prices are in hand it goes back to the normal interval"


def test_the_lifespan_reports_where_its_seconds_went():
    """A window nobody can attribute gets guessed at. The next deploy must
    say which phase it spent the time in."""
    src = open("app/main.py").read()
    for phase in ('_mark("init_db")', '_mark("remote_config")',
                  '_mark("pricing")', '_mark("welcome_email")'):
        assert phase in src, f"{phase} is not timed"
    assert "startup_complete seconds=" in src
