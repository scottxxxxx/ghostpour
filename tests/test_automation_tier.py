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
