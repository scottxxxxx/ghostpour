"""Project chat entry flow (Scott via CQ, 2026-08-26): the coach mark, the
context-range labels and the quick prompt chips are SERVED copy under
feature_definitions.project_chat.entry, in every locale, dashboard-
editable like the other cta_strings. {start}/{end}/{window_days} are the
client's to fill (window_days from the tier's own recall_max_age_days),
so the renderer must leave them alone; and the Free window is an explicit
null so no client has to treat "absent" as a special case.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent / "config" / "remote"
LOCALES = ("tiers", "tiers.es", "tiers.fr", "tiers.ja")
KEYS = {"coach_mark_title", "coach_mark_body", "coach_mark_dismiss_label",
        "context_range_label", "context_range_windowed_label", "context_range_empty_label", "quick_prompts"}


def _entry(loc):
    return json.loads((ROOT / f"{loc}.json").read_text())["feature_definitions"]["project_chat"]["entry"]


@pytest.mark.parametrize("loc", LOCALES)
def test_every_locale_serves_the_same_entry_keys_with_the_client_placeholders(loc):
    e = _entry(loc)
    assert set(e) == KEYS, loc
    assert "{start}" in e["context_range_label"] and "{end}" in e["context_range_label"]
    assert all(p in e["context_range_windowed_label"] for p in ("{window_days}", "{start}", "{end}"))
    # {recall_window_days} is GP-filled from the UPGRADE TARGET's dial; a label about the
    # user's own tier must not use it (Free could be enabled with 15 days by dial alone)
    assert "{recall_window_days}" not in json.dumps(e)
    assert [q["id"] for q in e["quick_prompts"]] == ["evolved", "decisions"]
    assert all({"id", "label", "prompt"} <= set(q) and q["label"] and q["prompt"] for q in e["quick_prompts"])


@pytest.mark.parametrize("loc", LOCALES)
def test_no_dashes_in_the_served_entry_copy(loc):
    blob = json.dumps(_entry(loc), ensure_ascii=False)
    assert "—" not in blob and "–" not in blob, loc
    # a hyphen may only sit inside a word, never as punctuation
    import re
    assert not re.search(r"(^|\s)-(\s|$)|\s-\w|\w-\s", blob), loc


@pytest.mark.parametrize("loc", LOCALES)
def test_the_window_dial_is_explicit_on_every_tier_and_null_means_no_ceiling(loc):
    d = json.loads((ROOT / f"{loc}.json").read_text())
    for tier in ("free", "plus", "pro"):
        cq = d["tiers"][tier]["feature_definitions"]["context_quilt"]
        assert "recall_max_age_days" in cq, (loc, tier)
        v = cq["recall_max_age_days"]
        assert v is None or (isinstance(v, int) and v >= 1), (loc, tier, v)
    assert d["tiers"]["free"]["feature_definitions"]["context_quilt"]["recall_max_age_days"] is None


def test_the_served_bundle_carries_entry_and_leaves_the_client_placeholders_alone(client):
    from app.services.recall_window import render_recall_window_copy
    from app.main import app as _app
    rc = _app.state.remote_configs
    for path, hdr in (("/v1/config/tiers", {}), ("/v1/config/tiers", {"Accept-Language": "fr"}), ("/v1/tiers", {})):
        r = client.get(path, headers=hdr)
        assert r.status_code == 200, path
        body = r.json()
        e = body["feature_definitions"]["project_chat"]["entry"]
        assert "{window_days}" in e["context_range_windowed_label"] and "{start}" in e["context_range_label"], path
        assert "{recall_window_days}" not in r.text, path
        assert body["tiers"]["free"]["feature_definitions"]["context_quilt"]["recall_max_age_days"] is None, path
