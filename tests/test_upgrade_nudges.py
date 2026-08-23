"""Upgrade nudges: a specific, earned reason at the moment it bites.

Before 2026-08-23 the entire Plus-to-Pro path was one trigger, search-cap
exhaustion, and the context block told every tier "trim" even when the
next tier would have fit. These pin the two primitives and the one
trigger that fires for real today.
"""
from __future__ import annotations

import json

import pytest

from app.services import upgrade_nudges as un

RC = {"tiers": {}}  # no served copy -> code defaults, which is the floor


def _caps(table):
    return lambda tier: table.get(tier)


# --- next_tier_that_fits -----------------------------------------------------

def test_names_the_lowest_higher_tier_that_fits():
    caps = _caps({"free": 200_000, "plus": 600_000, "pro": 1_440_000})
    assert un.next_tier_that_fits(RC, "free", 300_000, caps) == ("plus", 600_000)
    assert un.next_tier_that_fits(RC, "free", 900_000, caps) == ("pro", 1_440_000)
    assert un.next_tier_that_fits(RC, "plus", 900_000, caps) == ("pro", 1_440_000)


def test_never_recommends_a_tier_that_would_fail_the_same_way():
    """The whole point. 2,000,000 chars fits nobody, so the honest answer
    is None and the block stays a plain trim with no false affordance."""
    caps = _caps({"free": 200_000, "plus": 600_000, "pro": 1_440_000})
    assert un.next_tier_that_fits(RC, "free", 2_000_000, caps) is None
    assert un.next_tier_that_fits(RC, "plus", 2_000_000, caps) is None


def test_the_top_tier_has_nothing_to_be_nudged_to():
    caps = _caps({"free": 200_000, "plus": 600_000, "pro": 1_440_000})
    assert un.next_tier_that_fits(RC, "pro", 100, caps) is None


def test_uncapped_always_fits():
    caps = _caps({"free": 200_000, "plus": -1, "pro": None})
    assert un.next_tier_that_fits(RC, "free", 10**9, caps) == ("plus", -1)


def test_unknown_tiers_are_never_nudged():
    caps = _caps({"free": 1, "plus": 2, "pro": 3})
    assert un.next_tier_that_fits(RC, "admin", 10, caps) is None
    assert un.next_tier_that_fits(RC, "automation", 10, caps) is None


# --- context_upgrade_action ---------------------------------------------------

def test_context_action_carries_both_numbers_and_the_plan():
    caps = _caps({"free": 200_000, "plus": 600_000, "pro": 1_440_000})
    a = un.context_upgrade_action(RC, "plus", 900_000, caps)
    assert a["action"] == "open_paywall" and a["plan"] == "pro"
    assert a["label"] == "See Pro"
    assert "900K" in a["reason"] and "1440K" in a["reason"] and "Pro" in a["reason"]


def test_context_action_is_absent_when_nothing_fits():
    caps = _caps({"free": 200_000, "plus": 600_000, "pro": 1_440_000})
    assert un.context_upgrade_action(RC, "plus", 2_000_000, caps) is None


def test_served_copy_wins_over_the_code_default():
    rc = {"tiers": {"upgrade_nudges": {"context_fits_higher": {
        "text": "SERVED {needed} {cap} {tier_name}", "label": "Go {tier_name}"}}}}
    caps = _caps({"free": 200_000, "plus": 600_000, "pro": 1_440_000})
    a = un.context_upgrade_action(rc, "free", 300_000, caps)
    assert a["reason"] == "SERVED 300 600 Plus"
    assert a["label"] == "Go Plus"


def test_a_locale_that_skips_a_placeholder_does_not_crash():
    """Spanish and French copy does not pluralise by suffix and must be
    free to ignore {plural}. A KeyError here would 500 a chat turn, which
    is the one outcome worse than no nudge."""
    rc = {"tiers": {"upgrade_nudges": {"memory_excluded_scope": {
        "text": "Omitidas: {excluded}", "label": "Ver Plus"}}}}
    s = un.memory_excluded_cta(rc, "free", {"by_scope": {"meetings": 3}}, None)
    assert s["cta"]["text"] == "Omitidas: 3"


# --- memory_excluded_cta ------------------------------------------------------

def test_free_scope_exclusion_nudges_to_plus_with_the_number():
    s = un.memory_excluded_cta(RC, "free", {"by_scope": {"meetings": 6}}, None)
    assert s["feature"] == "context_quilt" and s["state"] == "teaser"
    assert s["cta"]["kind"] == "memory_excluded_scope"
    assert "6 earlier meetings" in s["cta"]["text"]
    assert s["cta"]["primary_action"] == {"label": "See Plus", "action": "open_paywall", "plan": "plus"}
    assert s["cta"]["details"]["excluded_meetings"] == 6


def test_plus_window_exclusion_nudges_to_pro_with_the_number_and_the_window():
    s = un.memory_excluded_cta(RC, "plus", {"by_window": {"meetings": 4}}, 30)
    assert s["cta"]["kind"] == "memory_excluded_window"
    assert "4 matching meetings" in s["cta"]["text"] and "30 days" in s["cta"]["text"]
    assert s["cta"]["primary_action"]["plan"] == "pro"
    assert s["cta"]["details"] == {"excluded_meetings": 4, "window_days": 30}


def test_singular_reads_as_singular():
    s = un.memory_excluded_cta(RC, "free", {"by_scope": {"meetings": 1}}, None)
    assert "1 earlier meeting that" in s["cta"]["text"]


@pytest.mark.parametrize("tier,excluded,window", [
    ("free", {"by_scope": {"meetings": 0}}, None),        # zero is silence
    ("free", {"by_window": {"meetings": 5}}, None),        # wrong predicate for the tier
    ("plus", {"by_scope": {"meetings": 5}}, 30),           # wrong predicate for the tier
    ("plus", {"by_window": {"meetings": 5}}, None),        # no window dial -> nothing to sell
    ("pro", {"by_window": {"meetings": 5}}, 30),           # nothing above Pro
    ("free", None, None),                                  # CQ has not shipped the field
    ("free", {"by_scope": {"meetings": "6"}}, None),       # a string is not a count
    ("free", {"by_scope": {"meetings": True}}, None),      # a bool is not a count
])
def test_silence_in_every_case_that_is_not_an_earned_number(tier, excluded, window):
    assert un.memory_excluded_cta(RC, tier, excluded, window) is None


# --- the trigger that fires for real today: the context block -----------------

def test_context_block_on_plus_names_pro_when_pro_fits(client, plus_user, monkeypatch):
    """Drives POST /v1/chat with Project Chat context past the Plus cap but
    inside Pro's, through the real route. Before this, Plus got "deselect
    meetings" with no mention that a tier existed where it fit."""
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = json.loads(json.dumps(cc.get("limits") or {}))
    cc.setdefault("limits", {}).setdefault("project_chat", {})["max_input_chars"] = {
        "free": 200_000, "plus": 600_000, "pro": 1_440_000}
    try:
        big = "x" * 700_000
        r = client.post("/v1/chat", json={
            "provider": "anthropic", "model": "auto",
            "system_prompt": "s", "user_content": big,
            "metadata": {"prompt_mode": "ProjectChat"},
        }, headers=plus_user["headers"])
        assert r.status_code == 413, r.text
        fs = r.json()["detail"]["feature_state"]
        cta = fs["cta"]
        assert cta["action"] == "trim_context", "trim stays primary"
        assert cta["secondary_action"]["plan"] == "pro"
        assert cta["secondary_action"]["action"] == "open_paywall"
        assert "700K" in cta["text"] and "1440K" in cta["text"] and "Pro" in cta["text"]
        assert fs["details"]["fits_on"] == "pro"
    finally:
        cc["limits"] = original


def test_context_block_on_pro_offers_nothing_because_nothing_fits(client, pro_user):
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = json.loads(json.dumps(cc.get("limits") or {}))
    cc.setdefault("limits", {}).setdefault("project_chat", {})["max_input_chars"] = {
        "free": 200_000, "plus": 600_000, "pro": 1_440_000}
    try:
        r = client.post("/v1/chat", json={
            "provider": "anthropic", "model": "auto",
            "system_prompt": "s", "user_content": "x" * 1_500_000,
            "metadata": {"prompt_mode": "ProjectChat"},
        }, headers=pro_user["headers"])
        assert r.status_code == 413
        cta = r.json()["detail"]["feature_state"]["cta"]
        assert "secondary_action" not in cta
        assert "fits_on" not in r.json()["detail"]["feature_state"]["details"]
        assert "Deselect" in cta["text"]
    finally:
        cc["limits"] = original
