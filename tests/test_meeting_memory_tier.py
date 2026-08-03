"""Meeting Memory is a Plus feature (Scott, 2026-08-03).

Plus used to resolve to `teaser` for context_quilt, which meant no recall,
no capture, and an upsell message. Combined with People being on for every
tier, a paying Plus subscriber would have landed on a People tab emptier
than a free user's, because People is a read over data that only exists if
extraction ran.

Nothing pinned the old value, so nothing would have pinned the new one.
These tests pin both halves of the change, and specifically the half that
went wrong before: the entitlement and the copy describing it drifting
apart, which is what left the registry advertising People to everyone
while calling the system that powers it Pro-only.
"""

import json
from pathlib import Path

import pytest
import yaml

_REMOTE = Path(__file__).parent.parent / "config" / "remote"
LOCALES = ["en", "es", "ja", "fr"]

ENTITLEMENTS = json.loads((_REMOTE / "entitlements.json").read_text())
TIERS = {loc: json.loads(
    (_REMOTE / ("tiers.json" if loc == "en" else f"tiers.{loc}.json")).read_text())
    for loc in LOCALES}
FEATURES = yaml.safe_load(
    (Path(__file__).parent.parent / "config" / "features.yml").read_text())


def test_plus_gets_meeting_memory():
    """The change itself. `teaser` here means no recall and no capture, so
    a Plus subscriber generates nothing for People to read."""
    row = ENTITLEMENTS["matrix"]["context_quilt"]
    assert row["plus"] == "enabled"
    assert row["pro"] == "enabled"


def test_free_stays_quota_gated_rather_than_open():
    """Free is deliberately `disabled` plus a monthly quota, which is the
    existing upsell design. Opening it is a cost decision, not a copy fix."""
    assert ENTITLEMENTS["matrix"]["context_quilt"]["free"] == "disabled"
    assert FEATURES["features"]["context_quilt"]["free_quota_per_month"] >= 1


@pytest.mark.parametrize("locale", LOCALES)
def test_upsell_names_the_tier_that_actually_unlocks_it(locale):
    """The bug CQ caught: the registry advertised a feature at one tier
    while its copy named another. A free user told to upgrade to Pro would
    be overpaying for something Plus now includes."""
    cq = TIERS[locale]["feature_definitions"]["context_quilt"]
    blob = json.dumps(
        {"cta": cq.get("upgrade_cta"), "strings": cq.get("cta_strings", {})},
        ensure_ascii=False)
    assert "Pro" not in blob, (
        f"{locale} still sends Meeting Memory upsells to Pro, but Plus "
        "unlocks it now")
    assert "Plus" in blob, f"{locale} upsell names no tier"


def test_compiled_fallback_agrees_with_the_served_copy():
    """features.yml is what the client falls back to when the config is
    unavailable, so it must not keep selling the old tier."""
    cq = FEATURES["features"]["context_quilt"]
    blob = json.dumps({"cta": cq.get("upgrade_cta"),
                       "strings": cq.get("cta_strings", {})})
    assert "Upgrade to Pro" not in blob
    assert "Plus" in blob


def test_served_feature_prose_uses_no_dashes():
    """House style, and these strings are user-facing. The compiled
    fallback carried an em dash that the served copies did not."""
    for name, feature in FEATURES["features"].items():
        for key in ("display_name", "description", "teaser_description",
                    "upgrade_cta"):
            value = feature.get(key) or ""
            for ch in ("—", "–"):
                assert ch not in value, f"features.yml {name}.{key} has {ch!r}"
