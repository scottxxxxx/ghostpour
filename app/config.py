"""Application Settings — pydantic-settings shape.

Resolution order for each field (when not provided programmatically):
1. Environment variable (`CZ_<FIELD>` per `env_prefix` below).
2. `.env` file in cwd (loaded by pydantic-settings automatically).
3. **Secret Manager fallback** for fields listed in
   `_SECRET_MANAGER_MAPPINGS` — runs at `get_settings()` time before
   pydantic builds the instance. Lets `.env.prod` ship without
   plaintext secrets once the operator has provisioned the SM
   counterparts. The mapping is `CZ_FOO` → secret name `foo` (lower-
   kebab) by default; override on a case-by-case basis.

Field defaults are still required so pydantic's required-field
validation passes when a secret is absent in BOTH env AND SM (i.e.
local dev test runs where neither is wired). For required fields like
`jwt_secret`, the .env in tests still has to carry a value — same as
before.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # Apple Sign In
    apple_bundle_id: str = "com.example.myapp"

    # Provider API Keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    xai_api_key: str = ""
    deepseek_api_key: str = ""
    kimi_api_key: str = ""
    qwen_api_key: str = ""
    openrouter_api_key: str = ""  # Used by Context Quilt worker

    # Admin
    admin_key: str = ""

    # Context Quilt integration
    cq_base_url: str = ""              # e.g., "https://cq.example.com"
    cq_app_id: str = "cloudzap"        # App identifier for CQ (UUID or legacy string)
    cq_client_secret: str = ""         # Client secret for CQ JWT auth (empty = use X-App-ID fallback)
    cq_recall_timeout_ms: int = 200    # Max wait for CQ recall (ms)

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/ghostpour.db"

    # Pricing
    pricing_source_url: str = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )
    pricing_refresh_seconds: int = 86400  # 24 hours

    # Debug
    verbose_logging: bool = False       # Log full request/response bodies (set CZ_VERBOSE_LOGGING=true)

    # Config file paths
    tier_config_path: str = "config/tiers.yml"
    feature_config_path: str = "config/features.yml"
    provider_config_path: str = "config/providers.yml"

    model_config = {"env_prefix": "CZ_", "env_file": ".env", "extra": "ignore"}


# Map of CZ_<env var> → Secret Manager secret name. When the env var is
# absent (or empty) at startup, `_ensure_secrets_in_env()` fetches from
# SM and sets os.environ so pydantic Settings loads the value normally.
#
# Adding a secret here is the FIRST step of migrating it from
# .env.prod plaintext to SM:
#   1. Add a row here.
#   2. Provision the secret in SM (runbook in ghostpour-ops).
#   3. Remove the corresponding line from .env.prod on the VM.
#   4. Restart the container — startup will fetch from SM.
#
# Don't add CZ_GCP_PROJECT or other config-style vars here — those
# aren't secrets, they're configuration that's safe in env.
_SECRET_MANAGER_MAPPINGS: dict[str, str] = {
    "CZ_JWT_SECRET": "jwt-secret",
    "CZ_ADMIN_KEY": "admin-key",
    "CZ_ANTHROPIC_API_KEY": "anthropic-api-key",
    "CZ_OPENAI_API_KEY": "openai-api-key",
    "CZ_OPENROUTER_API_KEY": "openrouter-api-key",
    "CZ_GOOGLE_API_KEY": "google-api-key",
    "CZ_XAI_API_KEY": "xai-api-key",
    "CZ_DEEPSEEK_API_KEY": "deepseek-api-key",
    "CZ_KIMI_API_KEY": "kimi-api-key",
    "CZ_QWEN_API_KEY": "qwen-api-key",
    "CZ_CQ_CLIENT_SECRET": "cq-client-secret",
}


def _ensure_secrets_in_env() -> None:
    """Fill os.environ from Secret Manager for any mapping that's
    currently empty. Called once before Settings instantiation. Idempotent.

    Calls into `app.secrets.get_secret`, which itself prefers env over
    SM — so this loop is a no-op for any secret already wired in env.
    The whole point is to let `.env.prod` ship without these values
    (or with empty strings for them) and let SM fill in.

    Logs a structured INFO line for each secret that was filled from
    SM. Operators who migrated a secret from .env.prod to SM should
    expect to see one such line per restart per migrated secret —
    confirms the migration is wired correctly. The absence of the
    line for a secret means env was already populated (the env may be
    shadowing a rotated SM value — runbook section "shadow risk"
    explains the cleanup).
    """
    # Local import: avoid a circular dep with anything that imports config
    # before app.secrets is available, and keep import cost off the
    # `from app.config import Settings` path until startup.
    import logging
    from app.secrets import get_secret

    log = logging.getLogger(__name__)

    for env_var, secret_name in _SECRET_MANAGER_MAPPINGS.items():
        existing = os.environ.get(env_var, "").strip()
        if existing:
            continue
        # Pass env_var=None so get_secret skips the env check (it'd
        # short-circuit to "" anyway since we already know it's empty)
        # and goes straight to SM.
        sm_value = get_secret(secret_name)
        if sm_value:
            os.environ[env_var] = sm_value
            log.info(
                "secret_filled_from_sm env_var=%s sm_secret=%s len=%d",
                env_var, secret_name, len(sm_value),
            )


@lru_cache
def get_settings() -> Settings:
    _ensure_secrets_in_env()
    return Settings()
