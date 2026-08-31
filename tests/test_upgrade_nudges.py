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

# No served copy -> code defaults, which is the floor. The DIALS are what a
# nudge keys on (Scott via CQ, 2026-08-26): a memory mode per tier and a
# window per tier, both served, never a tier name.
def _rc(modes=None, windows=None, copy=None):
    modes = modes or {"free": "teaser", "plus": "enabled", "pro": "enabled"}
    windows = windows if windows is not None else {"plus": 30, "pro": None}
    tiers = {t: {"display_name": t.capitalize(),
                 "feature_definitions": {"context_quilt": {"recall_max_age_days": windows[t]}} if t in windows else {}}
             for t in ("free", "plus", "pro")}
    return {"entitlements": {"matrix": {"context_quilt": modes}},
            "tiers": {"tiers": tiers, **({"upgrade_nudges": copy} if copy else {})}}


RC = _rc()


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
    rc = {**RC, "tiers": {**RC["tiers"], **rc["tiers"]}}
    s = un.memory_excluded_cta(rc, "free", {"by_scope": {"meetings": 3}}, None)
    assert s["cta"]["text"] == "Omitidas: 3"


# --- memory_excluded_cta ------------------------------------------------------

def test_free_scope_exclusion_nudges_to_plus_with_the_number():
    s = un.memory_excluded_cta(RC, "free", {"by_scope": {"meetings": 6}}, None)
    assert s["feature"] == "context_quilt" and s["state"] == "teaser"
    assert s["cta"]["kind"] == "memory_excluded_scope"
    assert "Memory from 6 meetings in this project" in s["cta"]["text"]
    assert s["cta"]["primary_action"] == {"label": "See Plus", "action": "open_paywall", "plan": "plus"}
    assert s["cta"]["details"]["excluded_meetings"] == 6


def test_plus_window_exclusion_nudges_to_pro_with_the_number_and_the_window():
    s = un.memory_excluded_cta(RC, "plus", {"by_window": {"meetings": 4}}, 30)
    assert s["cta"]["kind"] == "memory_excluded_window"
    assert "4 meetings older than 30 days" in s["cta"]["text"]
    assert s["cta"]["primary_action"]["plan"] == "pro"
    assert s["cta"]["details"] == {"excluded_meetings": 4, "window_days": 30, "next_tier": "pro"}
    assert "Pro has no window" in s["cta"]["text"]


def test_singular_reads_as_singular():
    s = un.memory_excluded_cta(RC, "free", {"by_scope": {"meetings": 1}}, None)
    assert "1 meeting in this project" in s["cta"]["text"]


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


# --- the copy claims what CQ's definition supports (contract 2026-08-23) ------

# CQ's real block for Scott's ABM project: counts are MEETINGS IN THE
# PROJECT the tier could not use (by_scope: hold memory the People render
# cannot use; by_window: last observation older than the window). They are
# NOT matches that scored, so the copy may not say "found" or "matching".
CQ_EXCLUDED = {
    "by_window": {"meetings": 60, "oldest": "2026-04-21T16:05:23.078562+00:00",
                  "max_age_days": 30, "definition": "age predicate inverted over the project scope"},
    "by_scope": {"meetings": 67, "definition": "meetings in the project holding memory the People render cannot use"},
}


def test_cq_real_block_renders_with_its_extra_keys_ignored():
    free = un.memory_excluded_cta(RC, "free", CQ_EXCLUDED, None)
    plus = un.memory_excluded_cta(RC, "plus", CQ_EXCLUDED, 30)
    assert free["cta"]["details"]["excluded_meetings"] == 67
    assert plus["cta"]["details"] == {"excluded_meetings": 60, "window_days": 30, "next_tier": "pro"}


OVERCLAIMS = {
    "": ("found", "matching", "skipped"),
    "es": ("encontró", "coincidentes", "no usó"),
    "fr": ("trouvées", "correspondantes", "non utilisées"),
    "ja": ("見つけた", "該当する"),
}


@pytest.mark.parametrize("loc", list(OVERCLAIMS))
def test_served_copy_never_claims_the_count_is_matches(loc):
    with open(f"config/remote/tiers{'.' + loc if loc else ''}.json", encoding="utf-8") as f:
        nudges = json.load(f)["upgrade_nudges"]
    for kind in ("memory_excluded_scope", "memory_excluded_window"):
        text = nudges[kind]["text"]
        assert "{excluded}" in text, (loc, kind)
        for word in OVERCLAIMS[loc]:
            assert word not in text, (loc, kind, word)
    for kind in ("memory_excluded_scope", "memory_excluded_window"):
        for word in OVERCLAIMS[""]:
            assert word not in un.DEFAULT_COPY[kind]["text"], (kind, word)


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


# --- the dials, not the names (Scott via CQ, 2026-08-26) ------------------------

def test_free_enabled_with_a_window_is_nudged_by_window_to_the_next_wider_tier():
    rc = _rc(modes={"free": "enabled", "plus": "enabled", "pro": "enabled"}, windows={"free": 15, "plus": None, "pro": None})
    s = un.memory_excluded_cta(rc, "free", {"by_scope": {"meetings": 9}, "by_window": {"meetings": 2}}, 15)
    assert s["cta"]["kind"] == "memory_excluded_window"       # scope cannot apply: Free is enabled
    assert s["cta"]["primary_action"]["plan"] == "plus" and "Plus has no window" in s["cta"]["text"]
    assert "older than 15 days, outside the Free window" in s["cta"]["text"]


def test_free_teaser_with_a_window_still_sells_scope_first():
    rc = _rc(windows={"free": 15, "plus": 30, "pro": None})
    s = un.memory_excluded_cta(rc, "free", {"by_scope": {"meetings": 3}, "by_window": {"meetings": 8}}, 15)
    assert s["cta"]["kind"] == "memory_excluded_scope" and s["cta"]["primary_action"]["plan"] == "plus"


def test_the_window_target_is_the_next_tier_that_is_wider_and_says_its_window():
    rc = _rc(windows={"plus": 30, "pro": 60})
    s = un.memory_excluded_cta(rc, "plus", {"by_window": {"meetings": 4}}, 30)
    assert s["cta"]["primary_action"]["plan"] == "pro" and "Pro has a 60 day window" in s["cta"]["text"]
    same = _rc(windows={"plus": 30, "pro": 30})
    assert un.memory_excluded_cta(same, "plus", {"by_window": {"meetings": 4}}, 30) is None
    narrower = _rc(windows={"plus": 30, "pro": 7})
    assert un.memory_excluded_cta(narrower, "plus", {"by_window": {"meetings": 4}}, 30) is None


def test_scope_sells_the_lowest_higher_tier_whose_mode_is_enabled():
    rc = _rc(modes={"free": "teaser", "plus": "teaser", "pro": "enabled"})
    s = un.memory_excluded_cta(rc, "free", {"by_scope": {"meetings": 2}}, None)
    assert s["cta"]["primary_action"]["plan"] == "pro" and "Pro brings it" in s["cta"]["text"]
    s2 = un.memory_excluded_cta(rc, "plus", {"by_scope": {"meetings": 2}}, None)
    assert s2["cta"]["primary_action"]["plan"] == "pro"


def test_all_tiers_enabled_and_unlimited_means_no_memory_nudge_can_fire():
    rc = _rc(modes={"free": "enabled", "plus": "enabled", "pro": "enabled"}, windows={"free": None, "plus": None, "pro": None})
    for tier in ("free", "plus", "pro"):
        assert un.memory_excluded_cta(rc, tier, {"by_scope": {"meetings": 5}, "by_window": {"meetings": 5}}, None) is None


def test_nobody_enabled_above_means_silence_not_a_dead_end_paywall():
    rc = _rc(modes={"free": "teaser", "plus": "teaser", "pro": "teaser"})
    assert un.memory_excluded_cta(rc, "free", {"by_scope": {"meetings": 5}}, None) is None


def test_display_names_come_from_the_served_tiers():
    rc = _rc(); rc["tiers"]["tiers"]["pro"]["display_name"] = "Pro Max"
    s = un.memory_excluded_cta(rc, "plus", {"by_window": {"meetings": 1}}, 30)
    assert "Pro Max has no window" in s["cta"]["text"] and s["cta"]["primary_action"]["label"] == "See Pro Max"


def test_served_copy_with_the_new_placeholders_and_a_localized_window_phrase():
    copy = {"memory_excluded_window": {"text": "{tier_name}: {excluded} fuera de {window} días. {next_tier} {next_window}.",
                                       "label": "Ver {next_tier}", "next_window_none": "no tiene ventana",
                                       "next_window_days": "tiene una ventana de {n} días"}}
    rc = _rc(windows={"plus": 30, "pro": None}, copy=copy)
    s = un.memory_excluded_cta(rc, "plus", {"by_window": {"meetings": 2}}, 30)
    assert s["cta"]["text"] == "Plus: 2 fuera de 30 días. Pro no tiene ventana." and s["cta"]["primary_action"]["label"] == "Ver Pro"
    rc2 = _rc(windows={"plus": 30, "pro": 90}, copy=copy)
    assert "Pro tiene una ventana de 90 días." in un.memory_excluded_cta(rc2, "plus", {"by_window": {"meetings": 2}}, 30)["cta"]["text"]


def test_every_served_locale_uses_the_placeholders_not_tier_names():
    import json
    from pathlib import Path
    root = Path(__file__).parent.parent / "config" / "remote"
    for loc in ("tiers", "tiers.es", "tiers.fr", "tiers.ja"):
        u = json.loads((root / f"{loc}.json").read_text())["upgrade_nudges"]
        for key in ("memory_excluded_scope", "memory_excluded_window"):
            blob = u[key]["text"] + u[key]["label"]
            assert "{next_tier}" in blob and "{tier_name}" in u[key]["text"], (loc, key)
            for name in ("Free", "Plus", "Pro"):
                assert name not in blob, (loc, key, name)
        assert "{n}" in u["memory_excluded_window"]["next_window_days"], loc
        assert u["memory_excluded_window"]["next_window_none"], loc
