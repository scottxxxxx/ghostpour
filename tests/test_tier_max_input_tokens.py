"""Pin the per-tier max_input_tokens caps that ship as the Project Chat
context-cap defense-in-depth. Values must match what iOS reads from
tiers.json (the fuel gauge math depends on them lining up).

If these change, the iOS team needs to know — see
docs/wire-contracts/project-chat.md for the contract.
"""

from app.models.tier import load_tier_config


def test_tier_yaml_caps_match_ss_contract():
    """Free=50K, Plus=150K, Pro=360K.

    Pro DOUBLED from 180K on 2026-08-23 on Scott's direct instruction.
    That OVERRIDES the tier matrix sheet in `Subscription Info/`, which
    still records 180,000 in both the enforced and the proposed columns.
    The sheet is the documented ruling, so it is now stale, and this
    comment exists so the next person reading it does not "correct" prod
    back down. Sonnet 4.6 accepts 1,000,000 input tokens (confirmed
    against Anthropic's Models API, not from memory), so 360K is well
    inside the model's ceiling."""
    tc = load_tier_config("config/tiers.yml")
    assert tc.tiers["free"].max_input_tokens == 50_000
    assert tc.tiers["plus"].max_input_tokens == 150_000
    assert tc.tiers["pro"].max_input_tokens == 360_000


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
    # Japanese is deliberately HALF. The char cap in client-config.ja is
    # half the others because the chars/4 token heuristic underestimates
    # CJK, and this legacy mirror (= chars/4) has to track it or an older
    # iOS build shows a gauge denominator twice the server's real cap and
    # the user hits a 413 at half a full bar. Before 2026-08-23 tiers.ja
    # carried 180000 against a client-config.ja of 360000 chars (= 90000
    # tokens), so that skew was already live and this test's uniform
    # expectation was hiding it.
    per_locale = {
        "tiers.json":    {"free": 50_000, "plus": 150_000, "pro": 360_000},
        "tiers.es.json": {"free": 50_000, "plus": 150_000, "pro": 360_000},
        "tiers.fr.json": {"free": 50_000, "plus": 150_000, "pro": 360_000},
        "tiers.ja.json": {"free": 50_000, "plus": 150_000, "pro": 180_000},
    }
    for variant, expected in per_locale.items():
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
