"""Shared fixtures for integration tests.

Provides a fully initialized FastAPI TestClient with:
- Temp SQLite database (isolated per test)
- Mocked LLM provider (returns canned responses)
- Mocked CQ service (tracks recall/capture calls)
- Auth helpers (create users, generate valid JWTs)
"""

import os
import sqlite3
import tempfile
from collections.abc import Generator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.chat import ChatResponse


# ---------------------------------------------------------------------------
# App + DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    """Return a temp file path for an isolated SQLite database."""
    return str(tmp_path / "test.db")


@pytest.fixture
def app_env(tmp_db_path: str) -> Generator[dict, None, None]:
    """Set environment variables for the test app, then clean up."""
    env = {
        "CZ_JWT_SECRET": "test-secret-key-that-is-long-enough-for-hs256-validation",
        "CZ_APPLE_BUNDLE_ID": "com.test.app",
        "CZ_ADMIN_KEY": "test-admin-key",
        "CZ_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_db_path}",
        "CZ_CQ_BASE_URL": "http://cq-mock:8000",
        "CZ_CQ_APP_ID": "test-app",
        "CZ_CQ_RECALL_TIMEOUT_MS": "200",
    }
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Clear settings cache so new env vars are picked up
    from app.config import get_settings
    get_settings.cache_clear()

    yield env

    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


@pytest.fixture
def mock_provider():
    """Mock the ProviderRouter.route method to return a canned response."""
    canned = ChatResponse(
        text="Test response from mock provider.",
        input_tokens=100,
        output_tokens=50,
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    with patch(
        "app.services.provider_router.ProviderRouter.route",
        new_callable=AsyncMock,
        return_value=canned,
    ) as mock:
        mock.canned_response = canned
        yield mock


@pytest.fixture
def mock_cq():
    """Mock CQ recall and capture functions. Tracks calls."""
    recall_result = {
        "context": "User prefers concise answers. Met with Bob last Tuesday.",
        "matched_entities": ["Bob Martinez", "Widget Project"],
        "patch_count": 3,
    }
    with patch(
        "app.services.context_quilt.recall",
        new_callable=AsyncMock,
        return_value=recall_result,
    ) as mock_recall, patch(
        "app.services.context_quilt.capture",
        new_callable=AsyncMock,
    ) as mock_capture:
        yield {"recall": mock_recall, "capture": mock_capture}


@pytest.fixture
def mock_pricing():
    """Patch the PricingService to return a known cost."""
    with patch(
        "app.services.pricing.PricingService.start",
        new_callable=AsyncMock,
    ), patch(
        "app.services.pricing.PricingService.stop",
        new_callable=AsyncMock,
    ), patch(
        "app.services.pricing.PricingService.calculate_cost",
        return_value={"total_cost": 0.001, "input_cost": 0.0005, "output_cost": 0.0005},
    ), patch(
        "app.services.pricing.PricingService.is_loaded",
        new_callable=lambda: property(lambda self: True),
    ):
        yield


@pytest.fixture
def client(app_env, mock_provider, mock_pricing) -> Generator[TestClient, None, None]:
    """Create a TestClient with the real app, test DB, and mocked provider/pricing."""
    # Import app fresh so lifespan picks up test env
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def client_with_cq(app_env, mock_provider, mock_pricing, mock_cq) -> Generator[TestClient, None, None]:
    """TestClient with CQ mocked (for CQ integration tests)."""
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# User + Auth fixtures
# ---------------------------------------------------------------------------

def _jwt_token(user_id: str, secret: str = "test-secret-key-that-is-long-enough-for-hs256-validation") -> str:
    """Generate a valid JWT access token for the given user."""
    from app.services.jwt_service import JWTService
    svc = JWTService(secret=secret, algorithm="HS256", access_expire_minutes=60, refresh_expire_days=30)
    return svc.create_access_token(user_id)


def _insert_user(
    db_path: str,
    user_id: str = "test-user-001",
    tier: str = "free",
    monthly_limit: float = 0.05,
    monthly_used: float = 0.0,
    is_trial: bool = False,
    trial_cost_limit: float | None = None,
    simulated_tier: str | None = None,
    simulated_exhausted: bool = False,
) -> None:
    """Insert a test user directly into the database (sync, for fixtures)."""
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, display_name, tier, created_at, updated_at,
            is_active, monthly_cost_limit_usd, monthly_used_usd,
            overage_balance_usd, allocation_resets_at,
            simulated_tier, simulated_exhausted, is_trial)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?, ?)""",
        (
            user_id,
            f"apple_sub_{user_id}",
            f"{user_id}@test.com",
            "Test User",
            tier,
            now,
            now,
            monthly_limit,
            monthly_used,
            "2099-01-01T00:00:00Z",
            simulated_tier,
            1 if simulated_exhausted else 0,
            1 if is_trial else 0,
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def free_user(tmp_db_path: str) -> dict:
    """Create a free tier user and return auth headers + user info."""
    user_id = "test-free-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="free", monthly_limit=0.35)
    return {
        "headers": {"Authorization": f"Bearer {_jwt_token(user_id)}"},
        "user_id": user_id,
        "tier": "free",
    }


@pytest.fixture
def pro_user(tmp_db_path: str) -> dict:
    """Create a pro tier user (CQ enabled) and return auth headers."""
    user_id = "test-pro-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="pro", monthly_limit=5.10)
    return {
        "headers": {"Authorization": f"Bearer {_jwt_token(user_id)}"},
        "user_id": user_id,
        "tier": "pro",
    }


@pytest.fixture
def plus_user(tmp_db_path: str) -> dict:
    """Create a plus tier user (CQ teaser) and return auth headers."""
    user_id = "test-plus-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="plus", monthly_limit=2.40)
    return {
        "headers": {"Authorization": f"Bearer {_jwt_token(user_id)}"},
        "user_id": user_id,
        "tier": "plus",
    }


@pytest.fixture
def exhausted_user(tmp_db_path: str) -> dict:
    """Create a user who has exhausted their monthly allocation."""
    user_id = "test-exhausted-user"
    _insert_user(
        tmp_db_path,
        user_id=user_id,
        tier="free",
        monthly_limit=0.35,
        monthly_used=0.40,
    )
    return {
        "headers": {"Authorization": f"Bearer {_jwt_token(user_id)}"},
        "user_id": user_id,
        "tier": "free",
    }


@pytest.fixture
def trial_user(tmp_db_path: str) -> dict:
    """Create a trial user on plus tier."""
    user_id = "test-trial-user"
    _insert_user(
        tmp_db_path,
        user_id=user_id,
        tier="plus",
        monthly_limit=0.50,
        is_trial=True,
    )
    return {
        "headers": {"Authorization": f"Bearer {_jwt_token(user_id)}"},
        "user_id": user_id,
        "tier": "plus",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def chat_request(
    provider: str = "auto",
    model: str = "auto",
    system_prompt: str = "You are a helpful assistant.",
    user_content: str = "Hello, world.",
    **kwargs,
) -> dict:
    """Build a chat request body dict."""
    body = {
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "user_content": user_content,
    }
    body.update(kwargs)
    return body
