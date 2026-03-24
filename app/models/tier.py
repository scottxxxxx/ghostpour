from pydantic import BaseModel

import yaml


class TierDefinition(BaseModel):
    display_name: str
    description: str = ""
    default_model: str = ""
    monthly_cost_limit_usd: float = -1  # -1 = unlimited
    trial_cost_limit_usd: float | None = None  # Cap during free trial period
    daily_cost_limit_usd: float = -1    # -1 = unlimited
    daily_token_limit: int = -1         # -1 = unlimited
    requests_per_minute: int = 10
    summary_mode: str = "delta"         # "delta", "choice" (full or delta)
    summary_interval_minutes: int = 10
    allowed_providers: list[str] = []
    allowed_models: list[str] = []
    max_images_per_request: int = 0
    hours_per_month: int = -1           # -1 = unlimited, display only
    storekit_product_id: str = ""
    # Generic feature gating: feature_name -> "enabled" | "teaser" | "disabled"
    features: dict[str, str] = {}
    # Display bullets for subscription UI
    feature_bullets: list[str] = []

    def feature_state(self, feature_name: str) -> str:
        """Get the state of a feature for this tier. Defaults to 'disabled'."""
        return self.features.get(feature_name, "disabled")

    def is_feature_enabled(self, feature_name: str) -> bool:
        return self.feature_state(feature_name) == "enabled"

    def is_feature_teaser(self, feature_name: str) -> bool:
        return self.feature_state(feature_name) == "teaser"


class TierConfig(BaseModel):
    tiers: dict[str, TierDefinition]


def load_tier_config(path: str) -> TierConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return TierConfig(**data)
