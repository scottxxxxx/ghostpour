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
    # Max input tokens (chars/4 heuristic) accepted on /v1/chat ProjectChat
    # sends. Defense-in-depth guard against attaching too much context — iOS
    # enforces client-side via the fuel gauge using the same value, but we
    # also enforce server-side and return 413 with a context_too_large CTA.
    # -1 disables the cap (admin tier).
    max_input_tokens: int = -1
    storekit_product_id: str = ""       # StoreKit product ID for this tier
    app_product_ids: dict[str, str] = {}  # optional per-app overrides
    # Feature gating moved to the entitlements matrix (Phase 2,
    # feature-entitlements.md): app.services.entitlements.entitlement_state
    # reading the persistent `entitlements` remote config is the ONLY home —
    # no fallback field here, by decision (§5.1).
    # Display bullets for subscription UI
    feature_bullets: list[str] = []

    @property
    def all_product_ids(self) -> dict[str, str]:
        """All product IDs across apps. Falls back to storekit_product_id."""
        if self.app_product_ids:
            return self.app_product_ids
        if self.storekit_product_id:
            return {"default": self.storekit_product_id}
        return {}

class TierConfig(BaseModel):
    tiers: dict[str, TierDefinition]


def load_tier_config(path: str) -> TierConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return TierConfig(**data)
