"""Pin the per-tier max_input_tokens caps that ship as the Project Chat
context-cap defense-in-depth. Values must match what iOS reads from
tiers.json (the fuel gauge math depends on them lining up).

If these change, the iOS team needs to know — see
docs/wire-contracts/project-chat.md for the contract.
"""

from app.models.tier import load_tier_config


def test_tier_yaml_caps_match_ss_contract():
    """Free=50K, Plus=150K, Pro=180K — sourced from the budget-gate spec
    and confirmed with SS in the budget-gate PR comm."""
    tc = load_tier_config("config/tiers.yml")
    assert tc.tiers["free"].max_input_tokens == 50_000
    assert tc.tiers["plus"].max_input_tokens == 150_000
    assert tc.tiers["pro"].max_input_tokens == 180_000


def test_admin_tier_uncapped():
    """Admin tier omits the field — defaults to -1 (uncapped). Must not
    inherit any of the user-tier caps by accident."""
    tc = load_tier_config("config/tiers.yml")
    assert tc.tiers["admin"].max_input_tokens == -1


def test_tier_default_is_uncapped():
    """A TierDefinition built with no max_input_tokens defaults to -1 so
    new tiers added later don't accidentally inherit a Free-tier cap."""
    from app.models.tier import TierDefinition
    t = TierDefinition(display_name="x")
    assert t.max_input_tokens == -1


def test_remote_config_wire_path_matches_ss_contract():
    """SS reads max_input_tokens at tiers.{tier}.feature_definitions.project_chat.max_input_tokens
    in tiers.json (and locale variants). If this path changes, iOS breaks
    silently. Pin the wire path AND the values across all three locales."""
    import json
    expected = {"free": 50_000, "plus": 150_000, "pro": 180_000}
    for variant in ["tiers.json", "tiers.es.json", "tiers.ja.json"]:
        d = json.loads(open(f"config/remote/{variant}").read())
        for tier_name, expected_cap in expected.items():
            actual = (
                d.get("tiers", {})
                .get(tier_name, {})
                .get("feature_definitions", {})
                .get("project_chat", {})
                .get("max_input_tokens")
            )
            assert actual == expected_cap, (
                f"{variant} tiers.{tier_name}.feature_definitions.project_chat.max_input_tokens "
                f"= {actual!r}, expected {expected_cap}"
            )
