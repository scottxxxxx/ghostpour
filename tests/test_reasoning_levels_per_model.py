"""Per-model reasoningLevels coverage check.

Every model in model-capabilities.json with supportsReasoning=True must
declare a reasoningLevels array containing only valid level names. Models
with supportsReasoning=False must not declare reasoningLevels.

Three locale variants must agree on the levels (locales differ on copy,
not on capabilities).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

VALID_LEVELS = {"off", "low", "medium", "high"}

CAPABILITY_FILES = [
    "config/remote/model-capabilities.json",
    "config/remote/model-capabilities.es.json",
    "config/remote/tr-model-capabilities.json",
]


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


@pytest.mark.parametrize("path", CAPABILITY_FILES)
def test_every_reasoning_model_declares_levels(path):
    data = _load(path)
    for model_id, cap in data["models"].items():
        if not cap.get("supportsReasoning"):
            assert "reasoningLevels" not in cap or not cap["reasoningLevels"], (
                f"{path}:{model_id} has reasoningLevels but supportsReasoning is False"
            )
            continue
        levels = cap.get("reasoningLevels")
        assert isinstance(levels, list) and levels, (
            f"{path}:{model_id} supportsReasoning=True but reasoningLevels missing/empty"
        )


@pytest.mark.parametrize("path", CAPABILITY_FILES)
def test_levels_are_valid_values(path):
    data = _load(path)
    for model_id, cap in data["models"].items():
        levels = cap.get("reasoningLevels") or []
        for lvl in levels:
            assert lvl in VALID_LEVELS, (
                f"{path}:{model_id} has invalid level {lvl!r}; "
                f"allowed: {sorted(VALID_LEVELS)}"
            )


@pytest.mark.parametrize("path", CAPABILITY_FILES)
def test_no_default_value_anywhere(path):
    """The contract drops 'default' entirely. None should appear in any list."""
    data = _load(path)
    for model_id, cap in data["models"].items():
        levels = cap.get("reasoningLevels") or []
        assert "default" not in levels, (
            f"{path}:{model_id} contains 'default' — must be removed; "
            "iOS sends an explicit level only."
        )


def test_locales_agree_on_levels():
    """Spanish/Japanese/TR variants must declare identical reasoningLevels per model.

    Differing levels would be a real product bug — pricing/UI shouldn't
    differ by language.
    """
    en = _load("config/remote/model-capabilities.json")
    es = _load("config/remote/model-capabilities.es.json")
    tr = _load("config/remote/tr-model-capabilities.json")

    for model_id in en["models"]:
        en_levels = en["models"][model_id].get("reasoningLevels")
        for variant_name, variant in (("es", es), ("tr", tr)):
            v_levels = variant["models"].get(model_id, {}).get("reasoningLevels")
            assert v_levels == en_levels, (
                f"{model_id}: en has {en_levels!r}, {variant_name} has {v_levels!r}"
            )


def test_every_supportsreasoning_model_has_levels_in_resolver():
    """Every model marked supportsReasoning=True must be reachable through the
    server-side reasoning resolver — i.e. its provider must be one of the
    supported families in app.services.providers.reasoning.
    """
    en = _load("config/remote/model-capabilities.json")
    supported_providers = {
        "OpenAI", "Anthropic", "Google", "xAI",
        "Moonshot AI", "Alibaba (Qwen)", "DeepSeek",
    }
    for model_id, cap in en["models"].items():
        if not cap.get("supportsReasoning"):
            continue
        assert cap["provider"] in supported_providers, (
            f"{model_id} has supportsReasoning=True but provider "
            f"{cap['provider']!r} isn't in the resolver's supported list"
        )
