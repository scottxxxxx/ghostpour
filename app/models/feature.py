"""
Generic feature gating model.

Features have three states per tier:
  - enabled: fully active (check + apply)
  - teaser: run check, return metadata, skip apply (for upgrade nudges)
  - disabled: don't run at all
"""

from enum import Enum

import yaml
from pydantic import BaseModel


class FeatureState(str, Enum):
    enabled = "enabled"
    teaser = "teaser"
    disabled = "disabled"


class FeatureDefinition(BaseModel):
    display_name: str
    description: str = ""
    teaser_description: str = ""
    upgrade_cta: str = ""
    teaser_response: str = ""  # Canned chat-bubble text returned in lieu of an LLM call when feature is in "teaser" state
    category: str = ""
    service_module: str = ""
    capture_skip_modes: list[str] = []  # prompt_mode values that skip capture


class FeatureConfig(BaseModel):
    features: dict[str, FeatureDefinition]


def load_feature_config(path: str) -> FeatureConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return FeatureConfig(**data)
