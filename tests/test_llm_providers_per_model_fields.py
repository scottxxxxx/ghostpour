"""Schema tests for the per-model capability fields in llm-providers.json.

History:
- 2026-05-12 PR B: added 7 sampling/IO capability fields per SS audit so iOS
  could stop guessing per-model defaults.
- 2026-05-12 PR A1 (Option A consolidation): pulled `reasoningLevels` +
  `promptReserveTokens` over from `model-capabilities.json`, plus added a
  top-level `defaultPromptReserveTokens` fallback.

Invariants enforced:
- Every model declares all 9 per-model fields (no silent fallback to
  provider-level).
- Top-level `defaultPromptReserveTokens` is present (file-level fallback
  used when a per-model `promptReserveTokens` is null).
- Locale variants agree on capability values (locales differ only on copy).
- Anthropic adaptive-thinking models (Opus 4.7, Sonnet 4.6) have
  `temperatureDefault: null`. The Anthropic API accepts `temperature: 1.0`
  or an omitted temperature when adaptive thinking is active, and rejects
  any other value (400: "`temperature` may only be set to 1 when thinking
  is enabled or in adaptive mode" — verified via live API 2026-05-19).
  We omit the field as the cleanest UX since a slider locked at 1.0
  would be a no-op.
- `cacheControlSupported: true` only on Anthropic models and `cloudzap.auto`
  (which routes via GP and can splice cache_control server-side).
- `serverManaged: true` only on `cloudzap.auto`.
- `reasoningLevels` is either null or a non-empty list of strings. SS gates
  the picker on `supportsReasoning && !reasoningLevels.isEmpty`, so a model
  with `supportsReasoning: false` MUST have `reasoningLevels: null` and a
  model with non-null levels must have `supportsReasoning: true`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROVIDER_FILES = [
    "config/remote/llm-providers.json",
    "config/remote/llm-providers.es.json",
    "config/remote/llm-providers.ja.json",
]

REQUIRED_FIELDS = (
    "maxOutputTokens",
    "temperatureDefault",
    "maxImagesPerRequest",
    "streamingSupported",
    "toolUseSupported",
    "cacheControlSupported",
    "serverManaged",
    "reasoningLevels",
    "promptReserveTokens",
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

        # reasoningLevels: null, or a non-empty list of non-empty strings
        rl = m["reasoningLevels"]
        if rl is not None:
            assert isinstance(rl, list) and rl, (
                f"{path}:{mid} reasoningLevels={rl!r} must be null or non-empty list"
            )
            for entry in rl:
                assert isinstance(entry, str) and entry, (
                    f"{path}:{mid} reasoningLevels entry {entry!r} must be non-empty string"
                )

        # promptReserveTokens: null, or positive int
        prt = m["promptReserveTokens"]
        if prt is not None:
            assert isinstance(prt, int) and prt > 0, (
                f"{path}:{mid} promptReserveTokens={prt!r} must be positive int or null"
            )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_adaptive_thinking_models_have_null_temperature(path):
    data = _load(path)
    for _, m in _iter_models(data):
        if m["id"] in ANTHROPIC_ADAPTIVE_THINKING_MODELS:
            assert m["temperatureDefault"] is None, (
                f"{path}:{m['id']} is on Anthropic adaptive-thinking path — "
                "temperatureDefault must be null (API only accepts "
                "temperature=1.0 or omission with adaptive thinking; "
                "we omit so iOS doesn't render a no-op slider)"
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


# Locale-parity: fields allowed to differ between en, es, tr.
# Everything else must match — including field PRESENCE (`tokenLimitField`
# being absent in one locale but null in another counts as drift).
#
# `displayName` and `description` carry localized copy at the provider
# and model level. `notes` carries provider-level operator commentary
# that is sometimes localized. Everything else is wire-shape / capability
# data that must agree across locales.
PROVIDER_LEVEL_DIVERGENT = {"displayName", "notes"}
MODEL_LEVEL_DIVERGENT = {"displayName", "description"}


def _strip(obj: dict, divergent: set[str]) -> dict:
    return {k: v for k, v in obj.items() if k not in divergent}


def test_locales_agree_on_version():
    """All locale variants advance together. Any drift means a PR forgot
    one — PR #188 caught its own omission this way, but earlier PRs
    (#184, #187) shipped with tr lagging because the prior parity test
    only checked model sets, not version."""
    versions = {p: _load(p)["version"] for p in PROVIDER_FILES}
    assert len(set(versions.values())) == 1, (
        f"locale versions diverged: {versions}"
    )


def test_locales_agree_on_top_level_fields():
    """Top-level keys outside `providers` (e.g., `defaultPromptReserveTokens`)
    must be identical across locales."""
    en = _load("config/remote/llm-providers.json")
    es = _load("config/remote/llm-providers.es.json")
    ja = _load("config/remote/llm-providers.ja.json")
    en_top = {k: v for k, v in en.items() if k != "providers"}
    for variant_name, variant in (("es", es), ("ja", ja)):
        v_top = {k: v for k, v in variant.items() if k != "providers"}
        assert en_top == v_top, (
            f"{variant_name} top-level fields drift from en: "
            f"en={en_top}, {variant_name}={v_top}"
        )


def test_locales_agree_on_provider_level_fields():
    """Provider entries must match across locales except for displayName
    and notes (localized copy). Catches drift like the missing
    tokenLimitField in a locale variant after PR #187."""
    en = _load("config/remote/llm-providers.json")
    es = _load("config/remote/llm-providers.es.json")
    ja = _load("config/remote/llm-providers.ja.json")

    def _providers_by_id(data: dict) -> dict[str, dict]:
        return {
            p["id"]: _strip({k: v for k, v in p.items() if k != "models"},
                            PROVIDER_LEVEL_DIVERGENT)
            for p in data["providers"]
        }

    en_idx = _providers_by_id(en)
    for variant_name, variant in (("es", es), ("ja", ja)):
        v_idx = _providers_by_id(variant)
        assert set(en_idx) == set(v_idx), (
            f"{variant_name} has different provider set than en: "
            f"en={set(en_idx)}, {variant_name}={set(v_idx)}"
        )
        for pid, en_p in en_idx.items():
            v_p = v_idx[pid]
            assert en_p == v_p, (
                f"{variant_name}:{pid} provider-level fields drift from en. "
                f"en keys not in {variant_name}: "
                f"{set(en_p) - set(v_p)}; "
                f"{variant_name} keys not in en: "
                f"{set(v_p) - set(en_p)}; "
                f"diff: en={en_p}, {variant_name}={v_p}"
            )


def test_locales_agree_on_per_model_fields():
    """Per-model entries must match across locales except for displayName
    and description (localized copy). Field-level equality on EVERY
    field, not just the 9 in REQUIRED_FIELDS — so new fields added
    later (e.g., reasoningFamily for Rev 3) automatically participate
    in parity without test changes."""
    en = _load("config/remote/llm-providers.json")
    es = _load("config/remote/llm-providers.es.json")
    ja = _load("config/remote/llm-providers.ja.json")

    def _models_by_id(data: dict) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for prov in data["providers"]:
            for m in prov["models"]:
                out[f"{prov['id']}/{m['id']}"] = _strip(m, MODEL_LEVEL_DIVERGENT)
        return out

    en_idx = _models_by_id(en)
    for variant_name, variant in (("es", es), ("ja", ja)):
        v_idx = _models_by_id(variant)
        assert set(en_idx) == set(v_idx), (
            f"{variant_name} has different model set than en"
        )
        for key, en_m in en_idx.items():
            v_m = v_idx[key]
            assert en_m == v_m, (
                f"{variant_name}:{key} per-model fields drift from en. "
                f"en keys not in {variant_name}: {set(en_m) - set(v_m)}; "
                f"{variant_name} keys not in en: {set(v_m) - set(en_m)}; "
                f"differing values: "
                f"{ {k: (en_m.get(k), v_m.get(k)) for k in set(en_m) & set(v_m) if en_m[k] != v_m[k]} }"
            )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_default_prompt_reserve_tokens_present(path):
    """File-level fallback used when a model's `promptReserveTokens` is null.

    SS reads this as the canonical name `defaultPromptReserveTokens`.
    """
    data = _load(path)
    assert "defaultPromptReserveTokens" in data, (
        f"{path} missing top-level defaultPromptReserveTokens"
    )
    val = data["defaultPromptReserveTokens"]
    assert isinstance(val, int) and val > 0, (
        f"{path} defaultPromptReserveTokens={val!r} must be positive int"
    )


@pytest.mark.parametrize("path", PROVIDER_FILES)
def test_reasoning_levels_consistent_with_supports_reasoning(path):
    """Picker semantics: SS gates `picker(model)` on
    `supportsReasoning && !reasoningLevels.isEmpty`.

    Two invariants follow:
    - If `reasoningLevels` is a non-empty list, `supportsReasoning` MUST be true.
    - If `supportsReasoning` is false, `reasoningLevels` MUST be null (a
      non-null list with supportsReasoning=false is a contradiction).

    Note: `supportsReasoning: true` with `reasoningLevels: null` is allowed —
    that's the `cloudzap.auto` case (capable in principle, no picker exposed).
    """
    data = _load(path)
    for prov_id, m in _iter_models(data):
        mid = m["id"]
        supports = m["supportsReasoning"]
        levels = m["reasoningLevels"]
        if levels is not None and len(levels) > 0:
            assert supports is True, (
                f"{path}:{prov_id}/{mid} has reasoningLevels but "
                f"supportsReasoning={supports!r}"
            )
        if supports is False:
            assert levels is None, (
                f"{path}:{prov_id}/{mid} has supportsReasoning=false but "
                f"reasoningLevels={levels!r} (must be null)"
            )
