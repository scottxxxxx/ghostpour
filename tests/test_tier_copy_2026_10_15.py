"""Tier copy, 2026-10-15 (Scott, via SS): Context Quilt is named, and it is
the same on Plus and Pro; the 30 days is Project Chat's alone.

The copy and the split ship together: "Context Quilt on Plus and Pro
equally" is only true once the memory dial is null on Plus, so these tests
read both halves from the same bundle.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "config/remote"
LOCALES = ("", ".es", ".fr", ".ja")


def _d(suf):
    return json.loads((ROOT / f"tiers{suf}.json").read_text(encoding="utf-8"))


def test_the_context_quilt_bullet_is_identical_on_plus_and_pro_and_the_memory_dial_agrees():
    for suf in LOCALES:
        d = _d(suf)
        plus, pro = d["tiers"]["plus"], d["tiers"]["pro"]
        assert plus["feature_bullets"][0].startswith("Context Quilt"), suf
        assert plus["feature_bullets"][0] == pro["feature_bullets"][0], suf
        for t in (plus, pro):
            assert t["feature_definitions"]["context_quilt"]["recall_max_age_days"] is None, suf
        assert "{recall_window_days}" not in plus["feature_bullets"][0], suf


def test_project_chat_carries_the_window_and_only_on_plus():
    for suf in LOCALES:
        d = _d(suf)
        plus, pro = d["tiers"]["plus"], d["tiers"]["pro"]
        assert sum("{recall_window_days}" in b for b in plus["feature_bullets"]) == 1, suf
        assert not any("{recall_window_days}" in b for b in pro["feature_bullets"]), suf
        assert plus["feature_definitions"]["project_chat"]["meeting_window_days"] == 30, suf
        assert pro["feature_definitions"]["project_chat"]["meeting_window_days"] is None, suf


def test_status_items_name_context_quilt_and_add_reports_and_icloud():
    for suf in LOCALES:
        d = _d(suf)
        for t, state in (("free", "teaser"), ("plus", "enabled"), ("pro", "enabled")):
            si = d["tiers"][t]["status_items"]
            brain = [s for s in si if s["icon"] == "brain"]
            assert len(brain) == 1 and brain[0]["label"] == "Context Quilt" and brain[0]["state"] == state, (suf, t)
            icons = [s["icon"] for s in si]
            assert icons.count("share") == 1 and icons.count("icloud") == 1, (suf, t)
            assert icons.index("share") == icons.index("chat") + 1, (suf, t)


def test_items_mirror_bullets_and_no_dash_or_old_naming_survives():
    for suf in LOCALES:
        d = _d(suf)
        for t in ("free", "plus", "pro"):
            blk = d["tiers"][t]
            assert [i["label"] for i in blk["feature_items"]] == blk["feature_bullets"], (suf, t)
            text = json.dumps([blk["description"], blk["feature_bullets"], blk["status_items"]], ensure_ascii=False)
            assert "—" not in text and "–" not in text, (suf, t)
            assert "Meeting Memory" not in text and "every conversation" not in text, (suf, t)
        cq = d["feature_definitions"]["context_quilt"]
        assert cq["display_name"] == "Context Quilt", suf
        for s in [cq["upgrade_cta"]] + list(cq["cta_strings"].values()):
            assert "Context Quilt" in s and "{recall_window_days}" not in s, (suf, s)
