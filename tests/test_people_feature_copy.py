"""People: signed-out CTA copy and the entitlement row (2026-08-02).

SS asked GP to own the words that convince a signed-out user to sign in,
changeable without an app build. It rides `tiers.feature_definitions`,
which already carries four features and is already translated into every
language the app ships, so this is not a new localized surface.

People is enabled for every tier (Scott, 2026-08-02), so there is no
upgrade door. The only closed door is auth, which is why `signed_out` is
the whole payload and there is no teaser copy.
"""

import json
from pathlib import Path

import pytest

_REMOTE = Path(__file__).parent.parent / "config" / "remote"
LOCALES = ["en", "es", "ja", "fr"]

# Every language the app ships. A served surface that lacks one of these
# silently falls back to English for that market.
TIERS = {loc: json.loads(
    (_REMOTE / ("tiers.json" if loc == "en" else f"tiers.{loc}.json")).read_text())
    for loc in LOCALES}
ENTITLEMENTS = json.loads((_REMOTE / "entitlements.json").read_text())

REQUIRED_SIGNED_OUT_KEYS = {
    "headline", "body", "body_with_count", "cta_label", "cta_action"}
CTA_VOCABULARY = {"sign_in", "upgrade", "none"}


@pytest.mark.parametrize("locale", LOCALES)
def test_people_copy_exists_in_every_shipped_language(locale):
    assert "people" in TIERS[locale]["feature_definitions"], (
        f"{locale} has no People copy, so that market falls back to English")


@pytest.mark.parametrize("locale", LOCALES)
def test_signed_out_block_is_complete(locale):
    block = TIERS[locale]["feature_definitions"]["people"]["signed_out"]
    assert set(block) == REQUIRED_SIGNED_OUT_KEYS
    assert all(str(v).strip() for v in block.values())


@pytest.mark.parametrize("locale", LOCALES)
def test_cta_action_is_from_the_agreed_vocabulary(locale):
    action = TIERS[locale]["feature_definitions"]["people"]["signed_out"]["cta_action"]
    assert action in CTA_VOCABULARY
    # Auth is the only gate on People, so the CTA is always sign-in.
    assert action == "sign_in"


@pytest.mark.parametrize("locale", LOCALES)
def test_count_variant_has_the_slot_and_the_plain_one_does_not(locale):
    """Two strings, not one. "You've met 0 people" is an argument against
    signing in, so the client needs a version with no number in it."""
    block = TIERS[locale]["feature_definitions"]["people"]["signed_out"]
    assert "{count}" in block["body_with_count"], locale
    assert "{count}" not in block["body"], locale


@pytest.mark.parametrize("locale", LOCALES)
def test_no_stray_interpolation_slots(locale):
    """Only {count} is supplied by the client. Any other brace is a slot
    nobody fills, which ships to the user verbatim."""
    import re
    block = TIERS[locale]["feature_definitions"]["people"]["signed_out"]
    for key, value in block.items():
        for slot in re.findall(r"\{(\w+)\}", str(value)):
            assert slot == "count", f"{locale}.{key} has unfilled slot {{{slot}}}"


@pytest.mark.parametrize("locale", LOCALES)
def test_no_dashes_used_as_punctuation(locale):
    """House style, and it matters more here than usual: this copy is the
    first thing a signed-out user reads."""
    people = TIERS[locale]["feature_definitions"]["people"]
    blob = json.dumps(people, ensure_ascii=False)
    for ch in ("—", "–"):
        assert ch not in blob, f"{locale} People copy contains {ch!r}"


def test_people_is_enabled_for_every_tier():
    """Scott, 2026-08-02: on for all tiers. If this ever becomes gated, the
    signed_out block is no longer sufficient on its own and teaser copy has
    to land with it."""
    row = ENTITLEMENTS["matrix"]["people"]
    other = ENTITLEMENTS["matrix"]["meeting_reports"]
    assert set(row) == set(other), "People row is missing a tier"
    assert set(row.values()) == {"enabled"}


def test_served_over_the_config_endpoint(client):
    """Signed-out clients must be able to fetch this: /v1/config takes no
    auth, which is what makes serving signed-out copy possible at all."""
    r = client.get("/v1/config/tiers", headers={"X-App-ID": "shouldersurf"})
    assert r.status_code == 200
    people = r.json()["feature_definitions"]["people"]
    assert people["signed_out"]["cta_action"] == "sign_in"


def test_localized_copy_is_actually_translated():
    """A copy-paste of the English block is worse than no entry, because it
    looks handled."""
    en = TIERS["en"]["feature_definitions"]["people"]["signed_out"]
    for loc in ("es", "ja", "fr"):
        other = TIERS[loc]["feature_definitions"]["people"]["signed_out"]
        for key in ("headline", "body", "cta_label"):
            assert other[key] != en[key], f"{loc}.{key} is still English"


# --- the general rule, not just People ------------------------------
#
# SS asked what the SLA is on translation lag, having inferred from the
# version integers that English runs ahead. The integers are independent
# per-file counters and say nothing about content, but the underlying
# worry is fair: nothing structural stopped a new key from landing in
# English alone. These two make it structural, so the answer is "there is
# no lag" rather than a number we would have to keep promising.


def test_every_feature_definition_exists_in_every_shipped_language():
    en_keys = set(TIERS["en"]["feature_definitions"])
    for loc in ("es", "ja", "fr"):
        missing = en_keys - set(TIERS[loc]["feature_definitions"])
        assert not missing, (
            f"{loc} is missing {sorted(missing)}, so those markets get "
            "English until someone notices")


def test_user_facing_feature_strings_are_not_left_in_english():
    """Only prose is checked. `category` is a machine token and
    `display_name` is sometimes a deliberate loanword, so neither is
    evidence of an untranslated block."""
    PROSE = ("description", "teaser_description", "upgrade_cta")
    en_fd = TIERS["en"]["feature_definitions"]
    for loc in ("es", "ja", "fr"):
        for feature, en_def in en_fd.items():
            other = TIERS[loc]["feature_definitions"][feature]
            for key in PROSE:
                if key in en_def and key in other and en_def[key]:
                    assert other[key] != en_def[key], (
                        f"{loc}.{feature}.{key} is still the English string")


# --- shape compatibility with frozen builds -------------------------
#
# Build 803 is the App Store release and is frozen with the old
# all-or-nothing decoder: a field it declares non-optional that we omit
# does not degrade that field, it drops the whole tiers file to the copy
# compiled into the app and loops on every launch. Adding keys is safe
# (Swift ignores unknown ones); omitting keys the shipped type requires is
# not. We cannot read their decoder, so the rule is to match the shape of
# the entries that already ship.


def test_new_features_carry_every_key_the_existing_ones_share():
    """A new feature entry missing a key that every shipped entry has is
    the one shape change that can brick a frozen build."""
    for loc in LOCALES:
        fd = TIERS[loc]["feature_definitions"]
        assert len(fd) > 1, loc
        for name in fd:
            others = [set(v) for k, v in fd.items() if k != name]
            shared = set.intersection(*others)
            missing = shared - set(fd[name])
            assert not missing, (
                f"{loc}.{name} omits {sorted(missing)}, which every other "
                "feature carries; a frozen build may require them")


def test_people_upsell_names_no_tier():
    """People is enabled everywhere, so any tier named in its copy would be
    wrong the moment someone read it. Auth is the only gate."""
    for loc in LOCALES:
        ppl = TIERS[loc]["feature_definitions"]["people"]
        blob = json.dumps({"t": ppl.get("teaser_description"),
                           "u": ppl.get("upgrade_cta")}, ensure_ascii=False)
        for tier in ("Plus", "Pro"):
            assert tier not in blob, f"{loc} People copy names {tier}"
