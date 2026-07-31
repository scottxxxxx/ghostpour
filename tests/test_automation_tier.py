"""The automation tier: a capped, revocable credential for partner harnesses.

TR's harness is blocked on auth (Sign in with Apple in a simulator, plus a
24h access token), so they asked for a long-lived token. The interesting
constraint is the ceiling: Plus, Pro and admin are all cost-unlimited, so an
automation account on any of them would have NO cap. This tier exists to
carry a real one.

It mirrors Pro for entitlements and model routing on purpose: a harness that
exercises a synthetic lane is testing something nobody ships.
"""

from __future__ import annotations

import json

import yaml

TIERS = yaml.safe_load(open("config/tiers.yml"))["tiers"]
ENTITLEMENTS = json.load(open("config/remote/entitlements.json"))
ROUTING = json.load(open("config/remote/model-routing.json"))


def test_the_tier_carries_a_real_ceiling():
    assert TIERS["automation"]["monthly_cost_limit_usd"] == 2.0


def test_the_paid_tiers_are_uncapped_which_is_why_this_exists():
    # If this ever changes, revisit whether the tier is still needed.
    for name in ("plus", "pro", "admin"):
        assert TIERS[name]["monthly_cost_limit_usd"] == -1


def test_it_is_not_sellable():
    # No StoreKit product means it can never be purchased or mistaken for one.
    assert TIERS["automation"]["storekit_product_id"] == ""


def test_entitlements_mirror_pro():
    for feature, cells in ENTITLEMENTS["matrix"].items():
        if "pro" in cells:
            assert cells.get("automation") == cells["pro"], feature


def test_routing_mirrors_pro_for_every_app_and_call_type():
    for app, cfg in ROUTING["apps"].items():
        assert "automation" in cfg["tiers"], app
        for name, ct in cfg["call_types"].items():
            models = ct["models"]
            if "pro" in models:
                assert models.get("automation") == models["pro"], f"{app}.{name}"


def test_a_harness_account_is_revocable_by_deactivating_it():
    """is_active is checked on every authenticated request, so flipping it
    kills a long-lived token instantly without rotating the JWT secret."""
    src = open("app/dependencies.py").read()
    assert "if not user.is_active:" in src


def test_automation_traffic_is_excluded_from_the_signal_aggregates():
    """TR's harness runs on every change and tags every managed call, so its
    traffic would become a large share of the per-call-type and per-scenario
    signal we calibrate rubric anchors against. The cost cap protects the
    money; this protects the signal (their ask, 2026-07-31)."""
    src = open("app/routers/webhooks.py").read()
    excl = "AND user_id NOT IN (SELECT id FROM users WHERE tier = 'automation')"
    assert src.count(excl) == 2, "expected the two signal aggregates to exclude it"
    # and it must sit on the call_type and scenario queries specifically
    for marker in ("GROUP BY call_type", "GROUP BY scenario"):
        i = src.index(marker)
        assert excl in src[i - 700:i], marker


def test_the_spend_stays_visible():
    """Deliberately NOT excluded from the users list: the money is real and
    should stay countable, it just wears its own tier there."""
    src = open("app/routers/webhooks.py").read()
    users_q = src[src.index("lifetime_cost_usd"):src.index("lifetime_cost_usd") + 3000]
    assert "tier = 'automation'" not in users_q


def test_automation_is_published_with_a_full_feature_definitions_block():
    """The gap SS's image bug taught us to look for.

    Their ChatImageWire does an exact-slug lookup on
    tiers[slug].feature_definitions.images and falls back silently when the
    slug is absent or the block is missing. automation shipped published but
    EMPTY, so a harness on it would have silently used client defaults for
    image sizing, project chat input caps, search caps and generation caps,
    which is the opposite of the faithful lane this tier exists to provide.
    """
    served = json.load(open("config/remote/tiers.json"))["tiers"]
    assert "automation" in served
    pro_fd = served["pro"]["feature_definitions"]
    auto_fd = served["automation"]["feature_definitions"]
    assert set(auto_fd) == set(pro_fd), "automation must mirror pro's blocks"
    assert auto_fd["images"]["max_long_edge"] == pro_fd["images"]["max_long_edge"]
    assert auto_fd["images"]["jpeg_quality"] == pro_fd["images"]["jpeg_quality"]
    assert auto_fd["project_chat"] == pro_fd["project_chat"]
    assert auto_fd["search"]["searches_per_month"] == pro_fd["search"]["searches_per_month"]


def test_every_tier_users_can_hold_is_published_with_images():
    """A tier a user can be ON but that we do not publish means every
    client value keyed off the slug falls back silently. Audit, 2026-07-31:
    admin exists in tiers.yml and is deliberately unpublished, and no user
    is on it."""
    import yaml
    defined = set(yaml.safe_load(open("config/tiers.yml"))["tiers"])
    published = set(json.load(open("config/remote/tiers.json"))["tiers"])
    unpublished = defined - published
    assert unpublished == {"admin"}, (
        f"unexpected unpublished tier slugs: {unpublished}. Any tier a user "
        f"can hold must be published or its clients fall back silently.")
    for name, t in json.load(open("config/remote/tiers.json"))["tiers"].items():
        assert (t.get("feature_definitions") or {}).get("images"), name
