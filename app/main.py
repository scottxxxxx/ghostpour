import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.middleware.request_logging import RequestLoggingMiddleware
from app.models.feature import load_feature_config
from app.models.tier import load_tier_config
from app.routers import auth, chat, config, health, webhooks
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
    app.state.feature_config = load_feature_config(settings.feature_config_path)
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

    config.seed_remote_configs()
    app.state.remote_configs = config.load_remote_configs()

    # Register feature hooks
    feature_hooks: dict[str, object] = {}
    if settings.cq_base_url:
        from app.services.features.context_quilt_hook import ContextQuiltHook
        cq_feature_def = app.state.feature_config.features.get("context_quilt")
        feature_hooks["context_quilt"] = ContextQuiltHook(cq_feature_def)
    app.state.feature_hooks = feature_hooks

    pricing = PricingService(
        source_url=settings.pricing_source_url,
        refresh_interval=settings.pricing_refresh_seconds,
    )
    await pricing.start()
    app.state.pricing = pricing

    yield

    await pricing.stop()


app = FastAPI(
    title="GhostPour",
    description="Open-source LLM API gateway with auth, rate limiting, and multi-provider routing.",
    version="0.4.0",
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
app.include_router(config.router, tags=["config"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])

# Context Quilt proxy routes — only included when CQ is configured
if get_settings().cq_base_url:
    from app.routers import cq_proxy
    app.include_router(cq_proxy.router, prefix="/v1", tags=["context-quilt"])
