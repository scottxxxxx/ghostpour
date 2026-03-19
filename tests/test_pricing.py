"""Tests for the pricing service cost calculation."""

from app.services.pricing import PricingService


def _make_service(prices: dict) -> PricingService:
    svc = PricingService()
    svc._prices = prices
    return svc


SAMPLE_PRICES = {
    "openai/gpt-5.2": {
        "input_cost_per_token": 0.0000025,  # $2.50/M
        "output_cost_per_token": 0.00001,  # $10/M
        "cache_read_input_token_cost": 0.00000125,  # $1.25/M (50% discount)
    },
    "claude-sonnet-4-6": {
        "input_cost_per_token": 0.000003,
        "output_cost_per_token": 0.000015,
        "cache_read_input_token_cost": 0.0000003,  # 90% discount
        "cache_creation_input_token_cost": 0.00000375,
    },
    "openai/o3-mini": {
        "input_cost_per_token": 0.0000011,
        "output_cost_per_token": 0.0000044,
        "output_cost_per_reasoning_token": 0.0000044,
    },
}


def test_basic_cost_calculation():
    svc = _make_service(SAMPLE_PRICES)
    cost = svc.calculate_cost("openai", "gpt-5.2", {}, 1000, 500)

    assert cost["pricing_available"] is True
    assert cost["input_cost"] == round(1000 * 0.0000025, 8)
    assert cost["output_cost"] == round(500 * 0.00001, 8)
    assert cost["total_cost"] == round(cost["input_cost"] + cost["output_cost"], 8)
    assert cost["billable_input_tokens"] == 1000
    assert cost["billable_output_tokens"] == 500


def test_cached_tokens_reduce_cost():
    svc = _make_service(SAMPLE_PRICES)
    usage = {"prompt_tokens_details.cached_tokens": 400}
    cost = svc.calculate_cost("openai", "gpt-5.2", usage, 1000, 500)

    # 600 billed at full rate, 400 at cache rate
    assert cost["billable_input_tokens"] == 600
    expected_input = 600 * 0.0000025 + 400 * 0.00000125
    assert cost["input_cost"] == round(expected_input, 8)
    assert cost["cached_savings"] > 0


def test_anthropic_cache_fields():
    svc = _make_service(SAMPLE_PRICES)
    usage = {
        "cache_read_input_tokens": 300,
        "cache_creation_input_tokens": 100,
    }
    cost = svc.calculate_cost("anthropic", "claude-sonnet-4-6", usage, 1000, 500)

    assert cost["billable_input_tokens"] == 700  # 1000 - 300 cached
    # Input: 700 * full + 300 * cache_read + 100 * cache_creation
    expected_input = (
        700 * 0.000003 + 300 * 0.0000003 + 100 * 0.00000375
    )
    assert cost["input_cost"] == round(expected_input, 8)


def test_reasoning_tokens():
    svc = _make_service(SAMPLE_PRICES)
    usage = {"completion_tokens_details.reasoning_tokens": 200}
    cost = svc.calculate_cost("openai", "o3-mini", usage, 500, 300)

    # 100 regular output + 200 reasoning, both at same rate for o3-mini
    regular = 100 * 0.0000044
    reasoning = 200 * 0.0000044
    assert cost["output_cost"] == round(regular + reasoning, 8)


def test_unknown_model_returns_no_pricing():
    svc = _make_service(SAMPLE_PRICES)
    cost = svc.calculate_cost("unknown", "nonexistent-model", {}, 1000, 500)

    assert cost["pricing_available"] is False
    assert cost["total_cost"] == 0.0
    assert cost["billable_input_tokens"] == 1000


def test_zero_tokens():
    svc = _make_service(SAMPLE_PRICES)
    cost = svc.calculate_cost("openai", "gpt-5.2", {}, 0, 0)

    assert cost["total_cost"] == 0.0
    assert cost["billable_input_tokens"] == 0


def test_none_tokens():
    svc = _make_service(SAMPLE_PRICES)
    cost = svc.calculate_cost("openai", "gpt-5.2", {}, None, None)

    assert cost["total_cost"] == 0.0
    assert cost["billable_input_tokens"] == 0


def test_model_lookup_with_provider_prefix():
    svc = _make_service(SAMPLE_PRICES)
    # "openai/gpt-5.2" is keyed with prefix
    pricing = svc.get_model_pricing("openai", "gpt-5.2")
    assert pricing is not None
    assert pricing["input_cost_per_token"] == 0.0000025


def test_model_lookup_without_prefix():
    svc = _make_service(SAMPLE_PRICES)
    # "claude-sonnet-4-6" is keyed without prefix
    pricing = svc.get_model_pricing("anthropic", "claude-sonnet-4-6")
    assert pricing is not None
    assert pricing["input_cost_per_token"] == 0.000003


def test_is_loaded():
    svc = PricingService()
    assert svc.is_loaded is False
    svc._prices = {"model": {}}
    assert svc.is_loaded is True
