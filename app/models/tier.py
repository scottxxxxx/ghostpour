from pydantic import BaseModel

import yaml


class TierDefinition(BaseModel):
    display_name: str
    daily_token_limit: int
    requests_per_minute: int
    allowed_providers: list[str]
    allowed_models: list[str]
    max_images_per_request: int


class TierConfig(BaseModel):
    tiers: dict[str, TierDefinition]


def load_tier_config(path: str) -> TierConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return TierConfig(**data)
