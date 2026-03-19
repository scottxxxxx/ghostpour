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

    # Admin
    admin_key: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/cloudzap.db"

    # Pricing
    pricing_source_url: str = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )
    pricing_refresh_seconds: int = 86400  # 24 hours

    # Config file paths
    tier_config_path: str = "config/tiers.yml"
    provider_config_path: str = "config/providers.yml"

    model_config = {"env_prefix": "CZ_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
