"""Schema tests for the 7 per-model capability fields in llm-providers.json.

Added 2026-05-12 per SS audit response: iOS asked us to expose per-model
sampling/IO capability constraints so it can stop guessing.

Invariants enforced:
- Every model declares all 7 fields (no silent fallback to provider-level).
- Locale variants agree on capability values (locales differ only on copy).
- Anthropic adaptive-thinking models (Opus 4.7, Sonnet 4.6) have
  `temperatureDefault: null` — sending temperature with adaptive thinking
  is rejected by the Anthropic API.
- `cacheControlSupported: true` only on Anthropic models and `cloudzap.auto`
  (which routes via GP and can splice cache_control server-side).
- `serverManaged: true` only on `cloudzap.auto`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROVIDER_FILES = [
    "config/remote/llm-providers.json",
    "config/remote/llm-providers.es.json",
    "config/remote/tr-llm-providers.json",
]

REQUIRED_FIELDS = (
    "maxOutputTokens",
    "temperatureDefault",
    "maxImagesPerRequest",
    "streamingSupported",
    "toolUseSupported",
    "cacheControlSupported",
    "serverManaged",
)

ANTHROPIC_ADAPTIVE_THINKING_MODELS = {
    # Effort-path models — Anthropic rejects `temperature` when
    # `thinking: {type: "adaptive"}` is set.
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "anthropic/claude-opus-4.7",
    "anthropic/claude-sonnet-4.6",
}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _iter_models(data: dict):
    for prov in data["providers"]:
        for m in prov["models"]:
            yield prov["id"], m


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_every_model_declares_all_required_fields(path):
    data = _load(path)
    for prov_id, m in _iter_models(data):
        for field in REQUIRED_FIELDS:
            assert field in m, (
                f"{path}:{prov_id}/{m['id']} missing required field {field!r}"
            )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_field_types_are_sane(path):
    data = _load(path)
    for prov_id, m in _iter_models(data):
        mid = m["id"]
        # maxOutputTokens: positive int, or null (only for serverManaged)
        mot = m["maxOutputTokens"]
        if mot is None:
            assert m["serverManaged"] is True, (
                f"{path}:{mid} maxOutputTokens is null but not serverManaged"
            )
        else:
            assert isinstance(mot, int) and mot > 0, (
                f"{path}:{mid} maxOutputTokens={mot!r} must be positive int"
            )

        # temperatureDefault: 0.0..2.0 float, or null
        td = m["temperatureDefault"]
        if td is not None:
            assert isinstance(td, (int, float)) and 0.0 <= td <= 2.0, (
                f"{path}:{mid} temperatureDefault={td!r} out of range"
            )

        # maxImagesPerRequest: non-negative int, or null (for serverManaged)
        mi = m["maxImagesPerRequest"]
        if mi is None:
            assert m["serverManaged"] is True
        else:
            assert isinstance(mi, int) and mi >= 0, (
                f"{path}:{mid} maxImagesPerRequest={mi!r} must be non-negative int"
            )
            if mi > 0:
                assert m.get("supportsVision") is True, (
                    f"{path}:{mid} maxImagesPerRequest>0 but supportsVision is false"
                )

        # The four bools
        for field in ("streamingSupported", "toolUseSupported",
                      "cacheControlSupported", "serverManaged"):
            assert isinstance(m[field], bool), (
                f"{path}:{mid} {field}={m[field]!r} must be bool"
            )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_adaptive_thinking_models_have_null_temperature(path):
    data = _load(path)
    for _, m in _iter_models(data):
        if m["id"] in ANTHROPIC_ADAPTIVE_THINKING_MODELS:
            assert m["temperatureDefault"] is None, (
                f"{path}:{m['id']} is on Anthropic adaptive-thinking path — "
                "temperatureDefault must be null (API rejects temperature "
                "when thinking is adaptive)"
            )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_cache_control_only_on_anthropic_or_gp_managed(path):
    """cacheControlSupported is an Anthropic-specific concept. Only models
    that ultimately hit Anthropic should declare it true."""
    data = _load(path)
    for prov_id, m in _iter_models(data):
        if not m["cacheControlSupported"]:
            continue
        is_anthropic_native = prov_id == "anthropic"
        is_anthropic_via_or = m["id"].startswith("anthropic/")
        is_gp_managed = prov_id == "cloudzap" and m["id"] == "auto"
        assert is_anthropic_native or is_anthropic_via_or or is_gp_managed, (
            f"{path}:{prov_id}/{m['id']} has cacheControlSupported=true "
            "but isn't Anthropic-backed"
        )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_server_managed_only_on_cloudzap_auto(path):
    data = _load(path)
    for prov_id, m in _iter_models(data):
        if m["serverManaged"]:
            assert prov_id == "cloudzap" and m["id"] == "auto", (
                f"{path}:{prov_id}/{m['id']} has serverManaged=true; "
                "only cloudzap/auto should"
            )


def test_locales_agree_on_capability_fields():
    """All three locale variants must declare identical capability values per
    model — only displayName/description differ across locales."""
    en = _load("config/remote/llm-providers.json")
    es = _load("config/remote/llm-providers.es.json")
    tr = _load("config/remote/tr-llm-providers.json")

    def _models_by_id(data: dict) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for prov in data["providers"]:
            for m in prov["models"]:
                out[f"{prov['id']}/{m['id']}"] = m
        return out

    en_idx = _models_by_id(en)
    for variant_name, variant in (("es", es), ("tr", tr)):
        v_idx = _models_by_id(variant)
        assert set(en_idx) == set(v_idx), (
            f"{variant_name} has different model set than en"
        )
        for key, en_m in en_idx.items():
            v_m = v_idx[key]
            for field in REQUIRED_FIELDS:
                assert en_m[field] == v_m[field], (
                    f"{key}: en {field}={en_m[field]!r}, "
                    f"{variant_name} {field}={v_m[field]!r}"
                )


def test_version_bumped():
    """PR B bumps llm-providers.json to v10."""
    for path in PROVIDER_FILES:
        v = _load(path)["version"]
        assert v >= 10, f"{path} version={v} — expected >=10 after PR B"
