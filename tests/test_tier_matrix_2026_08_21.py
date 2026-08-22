"""The tier matrix Scott ruled on 2026-08-21 is what the bundles say.

Source of truth: ~/Desktop/ShoulderSurf_Tier_Matrix_2026-08-20.xlsx,
"proposed" columns. This pins every cell that config can express, so a
dial save or a bundle edit that drifts from the ruling is a red test,
not a screenshot. Cells that need code (Free documents at 5/mo and 10 MB,
Free recall as a teaser that keeps the People lane) are NOT pinned here
until they ship; see the PR that adds them.
"""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
ENT = json.loads((ROOT / "config/remote/entitlements.json").read_text())["matrix"]
TIERS = {loc: json.loads((ROOT / f"config/remote/tiers{loc}.json").read_text())["tiers"]
         for loc in ("", ".es", ".fr", ".ja")}
CC = json.loads((ROOT / "config/remote/client-config.json").read_text())
YML = yaml.safe_load((ROOT / "config/tiers.yml").read_text())["tiers"]


def _fd(loc, tier, feature, key):
    return ((TIERS[loc][tier].get("feature_definitions") or {}).get(feature) or {}).get(key)


def test_switches_match_the_sheet():
    assert {t: ENT["project_chat"][t] for t in ("free", "plus", "pro")} == {"free": "teaser", "plus": "enabled", "pro": "enabled"}
    assert {t: ENT["web_search"][t] for t in ("free", "plus", "pro")} == {"free": "enabled", "plus": "enabled", "pro": "enabled"}
    assert {t: ENT["people"][t] for t in ("free", "plus", "pro")} == {"free": "enabled", "plus": "enabled", "pro": "enabled"}
    assert {t: ENT["share"][t] for t in ("free", "plus", "pro")} == {"free": "enabled", "plus": "enabled", "pro": "enabled"}
    # Plus and Pro read memory; Free's teaser-with-People-lane is code, pinned when it ships.
    assert ENT["context_quilt"]["plus"] == "enabled" and ENT["context_quilt"]["pro"] == "enabled"


def test_dials_match_the_sheet_in_every_locale():
    for loc in TIERS:
        assert [_fd(loc, t, "search", "searches_per_month") for t in ("free", "plus", "pro")] == [5, 75, 120], loc
        assert [_fd(loc, t, "generation", "generations_per_month") for t in ("free", "plus", "pro")] == [5, None, 100], loc
        assert [_fd(loc, t, "project_chat", "max_input_tokens") for t in ("free", "plus", "pro")] == [50000, 150000, 180000], loc
        assert _fd(loc, "plus", "context_quilt", "recall_max_age_days") == 30, loc
        assert _fd(loc, "pro", "context_quilt", "recall_max_age_days") is None, loc


def test_boot_time_limits_match_the_sheet():
    assert [YML[t]["monthly_cost_limit_usd"] for t in ("free", "plus", "pro")] == [2.00, -1, -1]
    assert [YML[t]["requests_per_minute"] for t in ("free", "plus", "pro")] == [5, 20, 30]
    assert [YML[t]["max_images_per_request"] for t in ("free", "plus", "pro")] == [1, 3, 5]


def test_documents_and_generation_gates_match_the_sheet():
    assert CC["documents"]["min_tier"] == "plus"          # Free waits on the per-tier document dial
    assert CC["documents"]["generation"]["min_tier"] == "free"
    assert CC["documents"]["per_file_max_mb"] == 25


def _status(loc, tier, icon):
    rows = [r for r in TIERS[loc][tier].get("status_items", []) if r.get("icon") == icon]
    return rows[0] if rows else None


def test_paywall_cards_say_what_the_dials_enforce():
    """The cards are what SS renders on the paywall; every number on them
    must be the dial's number, and a row must not claim a feature the tier's
    switch or min_tier does not give it (the 'File generation: upgrade to
    Pro' on the Plus card lived for a day after generation moved)."""
    for loc in TIERS:
        for tier, n in (("free", 5), ("plus", 75), ("pro", 120)):
            row = _status(loc, tier, "search")
            assert row and str(n) in row["value"], (loc, tier, row)
        for tier in ("free", "plus", "pro"):
            row = _status(loc, tier, "filegen")
            assert row and "Pro" not in row["value"] and "pro" not in row["value"].lower(), (loc, tier, row)
            assert _status(loc, tier, "photo"), (loc, tier)
        assert _status(loc, "free", "filegen")["value"].count("5") >= 1, loc
        # documents: Plus and Pro carry the paperclip row; Free does NOT until the dial ships
        assert _status(loc, "plus", "paperclip") and _status(loc, "pro", "paperclip"), loc
        assert _status(loc, "free", "paperclip") is None, loc
        # Memory row: Plus and Pro enabled; Free has none while its cell is disabled
        assert _status(loc, "plus", "brain")["state"] == "enabled" and _status(loc, "pro", "brain")["state"] == "enabled", loc
        assert _status(loc, "free", "brain") is None, loc
