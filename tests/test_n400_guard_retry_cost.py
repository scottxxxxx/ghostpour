"""A guard retry row carries its own cost (Scott's 1.0.2 run, 2026-11-19, t_011
logged estimated_cost_usd NULL for the checkpoint retry)."""
import asyncio
from types import SimpleNamespace

from app.routers.chat import _log_guard_retry
from app.services.pricing import PricingService


def _pricing():
    svc = PricingService()
    svc._prices = {"claude-sonnet-5": {"input_cost_per_token": 0.000002,
                                       "output_cost_per_token": 0.00001,
                                       "cache_read_input_token_cost": 0.0000002}}
    return svc


class _Tracker:
    def __init__(self):
        self.logged = []

    async def log_usage(self, db, user_id, body, response, ms, **kw):
        self.logged.append(response)


def _run(pricing):
    tracker = _Tracker()
    body = SimpleNamespace(provider="anthropic", model="claude-sonnet-5")
    retry = SimpleNamespace(cost=None, input_tokens=5841, output_tokens=420,
                            usage={"cache_read_input_tokens": 42612})
    asyncio.run(_log_guard_retry(tracker, None, "u", body, retry, 0.0,
                                 "checkpoint", resolved=True, app_id="n400", pricing=pricing))
    return tracker.logged[0]


def test_retry_row_is_costed():
    cost = _run(_pricing()).cost
    assert cost["total_cost"] == round(5841 * 0.000002 + 42612 * 0.0000002 + 420 * 0.00001, 8)


def test_without_pricing_the_row_still_logs():
    assert _run(None).cost is None
