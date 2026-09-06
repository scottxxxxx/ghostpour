"""No ContextQuilt recall reaches the N-400 lane. Scott's ruling, 2026-09-06.

The risk: `users.tier` is shared across apps because Apple issues the SIWA
subject per developer team, so a ShoulderSurf Pro user arrives at N-400 as
Pro. `context_quilt` resolved to `enabled` for app=n400, and apps.yml gives
n400 NO `cq` entry, so `_cq_identity()` falls back to the DEFAULT
ShoulderSurf identity. Had that hook run, GP would have authenticated to
ContextQuilt as ShoulderSurf and pulled meeting memory into an immigration
interview.

⚠ THE TRAP THIS FILE EXISTS FOR: disabling `context_quilt` alone does NOT
stop the hook. chat.py falls through to the PEOPLE-scoped recall lane when
context_quilt is disabled, and that lane still calls hook.before_llm and
still resolves the same fallback identity. Both features must be off.

So these tests assert the PROPERTY (the hook cannot run for n400), not the
two config values, because the values are the current means and the
property is the ruling.
"""
import json
import pathlib

import pytest

from app.services.entitlements import entitlement_state, matrix_slug_for

TIERS = ("free", "plus", "pro", "admin", "automation")
FLAT = json.loads(pathlib.Path("config/remote/entitlements.json").read_text())
N400 = json.loads(pathlib.Path("config/remote/n400/entitlements.json").read_text())


def _run_hook_would_fire(configs, tier: str, app: str) -> bool:
    """Mirrors chat.py's gate, including the people fallback.

    If this file and chat.py ever disagree, the mirror is wrong and the
    test is worthless, so `test_the_mirror_matches_the_router` pins the
    fallback still exists in the source.
    """
    state = entitlement_state(configs, tier, "context_quilt", app)
    if state == "teaser":
        return False
    if state != "disabled":
        return True
    return entitlement_state(configs, tier, "people", app) == "enabled"


def _configs():
    """Built from the BUNDLED files, not load_remote_configs().

    The runtime overlay in data/ is gitignored and shadows the bundle
    locally, so a test reading it would pass or fail on whatever happens to
    be on this machine rather than on what ships. CI has no overlay; this
    reads what ships in both places.
    """
    return {"entitlements": FLAT, "n400/entitlements": N400}


def test_n400_reads_its_own_matrix():
    assert matrix_slug_for("n400") == "n400/entitlements"
    assert pathlib.Path("config/remote/n400/entitlements.json").exists()


@pytest.mark.parametrize("tier", TIERS)
def test_the_recall_hook_cannot_fire_for_n400_at_any_tier(tier):
    """The ruling, asserted as the property rather than as two values."""
    configs = _configs()
    assert not _run_hook_would_fire(configs, tier, "n400"), (
        f"recall would run for n400 at tier {tier}")


@pytest.mark.parametrize("tier", ("plus", "pro"))
def test_it_still_fires_for_shouldersurf(tier):
    """The guard must be N-400-shaped, not a global kill. If this goes red,
    the change reached the wrong app."""
    configs = _configs()
    assert _run_hook_would_fire(configs, tier, "shouldersurf")


def test_disabling_context_quilt_alone_would_NOT_have_closed_it():
    """The trap, pinned. With people still enabled, the people-scoped lane
    runs and reaches the same fallback identity."""
    configs = _configs()
    half_done = dict(N400)
    half_done["matrix"] = dict(N400["matrix"], people=FLAT["matrix"]["people"])
    patched = dict(configs, **{"n400/entitlements": half_done})
    assert _run_hook_would_fire(patched, "pro", "n400"), (
        "if this passes, the people fallback is gone and the comment above "
        "is stale; re-derive before trusting the single-feature version")


def test_the_mirror_matches_the_router():
    """The fallback this file mirrors must still exist in chat.py. A test
    that mirrors code it no longer resembles is worse than none."""
    src = pathlib.Path("app/routers/chat.py").read_text()
    assert 'if not run_hook and feature_name == "context_quilt":' in src
    assert '"people", _app(request)) == "enabled"' in src


def test_the_per_app_matrix_covers_every_flat_feature():
    """entitlement_matrix REPLACES, never merges. A feature added to the
    flat matrix and not here would resolve 'disabled' for N-400 silently."""
    assert set(N400["matrix"]) == set(FLAT["matrix"]), (
        "per-app matrix drifted from the flat one; it replaces rather than "
        "merges, so a missing feature goes dark for N-400 by accident")


def test_web_search_is_untouched():
    """Copied verbatim and deliberately unchanged."""
    assert N400["matrix"]["web_search"] == FLAT["matrix"]["web_search"]


def test_only_the_two_recall_features_differ_from_flat():
    changed = {f for f in FLAT["matrix"] if N400["matrix"][f] != FLAT["matrix"][f]}
    assert changed == {"context_quilt", "people"}, changed
