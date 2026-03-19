"""Unit tests for tier enforcement logic."""

import pytest
from fastapi import HTTPException

from app.models.chat import ChatRequest
from app.models.tier import TierDefinition
from app.services.usage_tracker import UsageTracker


@pytest.fixture
def tracker():
    return UsageTracker()


@pytest.fixture
def free_tier():
    return TierDefinition(
        display_name="Free",
        daily_token_limit=50000,
        requests_per_minute=5,
        allowed_providers=["openai", "anthropic"],
        allowed_models=["gpt-5-nano", "claude-haiku-4-5-20251001"],
        max_images_per_request=0,
    )


@pytest.fixture
def subscriber_tier():
    return TierDefinition(
        display_name="Subscriber",
        daily_token_limit=500000,
        requests_per_minute=30,
        allowed_providers=["*"],
        allowed_models=["*"],
        max_images_per_request=5,
    )


def test_free_tier_allows_valid_provider(tracker: UsageTracker, free_tier):
    request = ChatRequest(
        provider="openai",
        model="gpt-5-nano",
        system_prompt="test",
        user_content="test",
    )
    tracker.check_model_access(request, free_tier)  # Should not raise


def test_free_tier_blocks_disallowed_provider(tracker: UsageTracker, free_tier):
    request = ChatRequest(
        provider="xai",
        model="grok-4",
        system_prompt="test",
        user_content="test",
    )
    with pytest.raises(HTTPException) as exc:
        tracker.check_model_access(request, free_tier)
    assert exc.value.status_code == 403


def test_free_tier_blocks_disallowed_model(tracker: UsageTracker, free_tier):
    request = ChatRequest(
        provider="openai",
        model="gpt-5.2",
        system_prompt="test",
        user_content="test",
    )
    with pytest.raises(HTTPException) as exc:
        tracker.check_model_access(request, free_tier)
    assert exc.value.status_code == 403


def test_free_tier_blocks_images(tracker: UsageTracker, free_tier):
    request = ChatRequest(
        provider="openai",
        model="gpt-5-nano",
        system_prompt="test",
        user_content="test",
        images=["base64data"],
    )
    with pytest.raises(HTTPException) as exc:
        tracker.check_model_access(request, free_tier)
    assert exc.value.status_code == 403


def test_subscriber_allows_all(tracker: UsageTracker, subscriber_tier):
    request = ChatRequest(
        provider="xai",
        model="grok-4",
        system_prompt="test",
        user_content="test",
        images=["img1", "img2", "img3"],
    )
    tracker.check_model_access(request, subscriber_tier)  # Should not raise


def test_subscriber_blocks_too_many_images(tracker: UsageTracker, subscriber_tier):
    request = ChatRequest(
        provider="openai",
        model="gpt-5.2",
        system_prompt="test",
        user_content="test",
        images=["1", "2", "3", "4", "5", "6"],
    )
    with pytest.raises(HTTPException) as exc:
        tracker.check_model_access(request, subscriber_tier)
    assert exc.value.status_code == 403
