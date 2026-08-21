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


# --- The half #613 missed: the tier CARDS, not just the gate copy ---------
#
# #613 moved the upsell strings to Plus and pinned them, and the Plus card's
# own status row kept saying "Meeting Memory: upgrade to Pro, teaser" for
# seventeen days, in all four locales, until SS's empty-state pass put the
# served button ("Upgrade to Plus") on the same screen as it. The rows are
# hand-authored JSON; nothing derived them from the matrix, so nothing could
# disagree with the matrix out loud. These do.

_BRAIN = "brain"  # the icon every Meeting Memory row carries, in every locale


def _memory_status_row(tier: dict) -> dict | None:
    rows = [r for r in tier.get("status_items", []) if r.get("icon") == _BRAIN]
    assert len(rows) <= 1, "a tier card lists Meeting Memory twice"
    return rows[0] if rows else None


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("tier_name", ["free", "plus", "pro"])
def test_card_status_row_agrees_with_the_matrix(locale, tier_name):
    """The card's Memory row must say what the resolver will actually do
    for that tier. `teaser` on a card whose tier resolves `enabled` is the
    exact drift SS saw on-device."""
    matrix_state = ENTITLEMENTS["matrix"]["context_quilt"][tier_name]
    row = _memory_status_row(TIERS[locale]["tiers"][tier_name])
    if row is None:
        assert matrix_state == "disabled", (
            f"{locale}/{tier_name}: matrix says {matrix_state} but the card "
            "has no Meeting Memory row")
        return
    assert row["state"] == matrix_state, (
        f"{locale}/{tier_name}: card row state {row['state']!r} but the "
        f"matrix resolves {matrix_state!r}")
    if matrix_state == "enabled":
        assert "Pro" not in row["value"], (
            f"{locale}/{tier_name}: an enabled row still sends users to Pro")


@pytest.mark.parametrize("locale", LOCALES)
def test_pro_description_does_not_claim_memory_as_its_own(locale):
    """Pro's description read '...and Meeting Memory' as the thing Pro adds
    over Plus. Once Plus has it, that sentence is an upsell for nothing."""
    if ENTITLEMENTS["matrix"]["context_quilt"]["plus"] != "enabled":
        pytest.skip("only meaningful while Plus includes Memory")
    en_label = TIERS[locale]["tiers"]["pro"]
    memory_label = _memory_status_row(en_label)["label"]
    assert memory_label not in en_label["description"], (
        f"{locale}: pro description still names {memory_label!r} as Pro's "
        "differentiator")


@pytest.mark.parametrize("locale", LOCALES)
def test_plus_card_lists_memory_when_it_includes_it(locale):
    """If the matrix gives Plus Memory, the Plus feature list says so, the
    way Pro's already did. Otherwise a buyer comparing cards sees Memory
    on Pro only and draws the wrong conclusion from a true list."""
    if ENTITLEMENTS["matrix"]["context_quilt"]["plus"] != "enabled":
        pytest.skip("only meaningful while Plus includes Memory")
    plus = TIERS[locale]["tiers"]["plus"]
    brain_items = [i for i in plus["feature_items"] if i.get("icon") == _BRAIN]
    assert len(brain_items) == 1, f"{locale}: Plus feature_items has no Memory entry"
    assert brain_items[0]["label"] in plus["feature_bullets"], (
        f"{locale}: feature_items and feature_bullets disagree on the Memory line")
