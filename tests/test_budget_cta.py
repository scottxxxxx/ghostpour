"""Unit tests for the budget-exhausted CTA resolver.

The resolver pulls copy from `tiers.{locale}.json` →
`tiers.{tier}.feature_definitions.budget.cta_exhausted`, with a generic
English fallback when config is missing.
"""

from app.services.budget_cta import _FALLBACK_CTA, get_budget_exhausted_cta


def _tiers_config(locale_suffix: str = "", text: str = "Free user, upgrade.") -> dict:
    """Build a remote_configs dict shaped like what's loaded at startup."""
    payload = {
        "version": 1,
        "tiers": {
            "free": {
                "feature_definitions": {
                    "budget": {
                        "cta_exhausted": {
                            "kind": "budget_exhausted",
                            "text": text,
                            "action": "open_paywall",
                        }
                    }
                }
            }
        },
    }
    key = f"tiers.{locale_suffix}" if locale_suffix else "tiers"
    return {key: payload}


def test_returns_configured_cta_for_default_locale():
    cfg = _tiers_config()
    cta = get_budget_exhausted_cta(cfg, "free")
    assert cta["kind"] == "budget_exhausted"
    assert cta["text"] == "Free user, upgrade."
    assert cta["action"] == "open_paywall"


def test_returns_localized_cta_when_locale_variant_exists():
    cfg = {
        **_tiers_config(text="english copy"),
        **_tiers_config(locale_suffix="es", text="copia en español"),
    }
    cta = get_budget_exhausted_cta(cfg, "free", locale="es")
    assert cta["text"] == "copia en español"


def test_locale_en_uses_base_tiers_not_tiers_en():
    """`en` is the implicit default; the resolver should not look for
    a `tiers.en` key (none exists)."""
    cfg = _tiers_config(text="base")
    cta = get_budget_exhausted_cta(cfg, "free", locale="en")
    assert cta["text"] == "base"


def test_locale_falls_back_to_base_when_variant_missing():
    cfg = _tiers_config(text="base only")
    cta = get_budget_exhausted_cta(cfg, "free", locale="ja")
    assert cta["text"] == "base only"


def test_fallback_when_no_tiers_config_loaded():
    cta = get_budget_exhausted_cta({}, "free")
    assert cta == _FALLBACK_CTA


def test_fallback_when_tier_missing_from_config():
    cfg = _tiers_config()
    cta = get_budget_exhausted_cta(cfg, "plus")  # plus not in cfg
    assert cta == _FALLBACK_CTA


def test_fallback_when_budget_block_missing():
    cfg = {"tiers": {"version": 1, "tiers": {"free": {"feature_definitions": {}}}}}
    cta = get_budget_exhausted_cta(cfg, "free")
    assert cta == _FALLBACK_CTA


def test_returns_fresh_dict_so_callers_can_mutate_safely():
    cfg = _tiers_config()
    cta1 = get_budget_exhausted_cta(cfg, "free")
    cta1["text"] = "mutated"
    cta2 = get_budget_exhausted_cta(cfg, "free")
    assert cta2["text"] == "Free user, upgrade."  # original survives
