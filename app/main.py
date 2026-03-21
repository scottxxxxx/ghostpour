import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.middleware.request_logging import RequestLoggingMiddleware
from app.models.tier import load_tier_config
from app.routers import auth, chat, health, webhooks
from app.services.apple_auth import AppleAuthVerifier
from app.services.jwt_service import JWTService
from app.services.pricing import PricingService
from app.services.provider_router import ProviderRouter
from app.services.rate_limiter import RateLimiter
from app.services.usage_tracker import UsageTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Init database
    await init_db(settings.database_url)

    # Store services on app state
    app.state.settings = settings
    app.state.start_time = time.monotonic()
    app.state.tier_config = load_tier_config(settings.tier_config_path)
    app.state.apple_verifier = AppleAuthVerifier(settings.apple_bundle_id)
    app.state.jwt_service = JWTService(
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        access_expire_minutes=settings.jwt_access_token_expire_minutes,
        refresh_expire_days=settings.jwt_refresh_token_expire_days,
    )
    app.state.provider_router = ProviderRouter(
        settings.provider_config_path, settings
    )
    app.state.rate_limiter = RateLimiter()
    app.state.usage_tracker = UsageTracker()

    pricing = PricingService(
        source_url=settings.pricing_source_url,
        refresh_interval=settings.pricing_refresh_seconds,
    )
    await pricing.start()
    app.state.pricing = pricing

    yield

    await pricing.stop()


app = FastAPI(
    title="CloudZap",
    description="Open-source LLM API gateway with auth, rate limiting, and multi-provider routing.",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(chat.router, prefix="/v1", tags=["chat"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
