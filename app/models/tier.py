from pydantic import BaseModel

import yaml


class TierDefinition(BaseModel):
    display_name: str
    default_model: str = ""
    monthly_cost_limit_usd: float = -1  # -1 = unlimited
    daily_cost_limit_usd: float = -1    # -1 = unlimited
    daily_token_limit: int = -1         # -1 = unlimited
    requests_per_minute: int = 10
    summary_mode: str = "delta"         # "delta", "choice" (full or delta)
    summary_interval_minutes: int = 10
    allowed_providers: list[str] = []
    allowed_models: list[str] = []
    max_images_per_request: int = 0
    storekit_product_id: str = ""


class TierConfig(BaseModel):
    tiers: dict[str, TierDefinition]


def load_tier_config(path: str) -> TierConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return TierConfig(**data)
