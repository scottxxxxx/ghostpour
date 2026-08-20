"""Tech Rehearsal serves one model per call type, to everyone.

Scott's rule, 2026-08-19, and it is a product decision rather than an
engineering one: every TR user gets the best model for the job, cost is
not the constraint on WHICH model, and what varies between users is how
MUCH they get rather than how good it is. TR is explicitly not
ShoulderSurf, where the tier does buy a different model.

The consequence that makes it testable: the harness account has to
resolve to the same model as a real user, or nothing it verifies is
evidence about production. That is not hypothetical. Measured
2026-08-19, all fifteen TR call types carried an `automation` override
in the live routing config pointing at a model no user was on, so every
harness verification TR had ever run exercised a lane nobody uses,
including four scenario kinds they had certified that day.

Two ways a call type can break the rule and only one of them is visible
in the routing table:

1. A row that lists different models per tier. Obvious on sight.
2. NO ROW AT ALL. `_resolve_model_routing` falls back to
   `tier.default_model`, and those differ by design: haiku for free,
   plus and pro, sonnet-4-6 for automation and admin. So an unrouted
   call type silently splits users from the harness while the routing
   table looks clean. That is how tr_query, tr_summary and tr_analysis
   were splitting with real traffic on them.
"""

import json
import pathlib
import re

ROUTING = pathlib.Path("config/remote/model-routing.json")
TIERS = pathlib.Path("config/tiers.yml")


def _tr_call_types():
    return json.loads(ROUTING.read_text())["apps"]["techrehearsal"]["call_types"]


def test_no_tr_call_type_serves_a_different_model_by_tier():
    offenders = {}
    for ct, cfg in _tr_call_types().items():
        models = set((cfg.get("models") or {}).values())
        if len(models) > 1:
            offenders[ct] = sorted(models)
    assert not offenders, (
        f"these TR call types serve different models to different tiers: "
        f"{offenders}. On TR the tier buys volume, not quality, and a "
        f"harness on its own model verifies a lane nobody uses.")


def test_every_call_type_we_hold_a_prompt_for_has_a_row():
    """An absent row is the invisible half of the same bug: it falls back
    to `tier.default_model`, which is NOT uniform across tiers."""
    src = pathlib.Path("app/services/prompt_assembly.py").read_text()
    block = re.search(r"_CALL_TYPE_TO_CONFIG = \{(.*?)\n\}", src, re.S).group(1)
    owned = set(re.findall(r'"(tr_[a-z_]+)"\s*:', block))
    rows = set(_tr_call_types())
    missing = sorted(owned - rows)
    assert not missing, (
        f"{missing} have a GP-owned prompt but no routing row, so they "
        f"resolve through tier.default_model and split by tier silently")


def test_the_call_types_that_were_splitting_silently_are_pinned():
    """tr_query, tr_summary and tr_analysis had live traffic and no row,
    so users were resolving to one model and the harness to another with
    nothing in the routing table showing it. Pinned to what users were
    already getting, so the fix corrected the split without also moving
    anyone's quality."""
    rows = _tr_call_types()
    for ct in ("tr_query", "tr_summary", "tr_analysis"):
        assert ct in rows, f"{ct} lost its row and is falling through again"
        assert len(set(rows[ct]["models"].values())) == 1, ct


def test_the_tier_defaults_really_do_differ():
    """The reason an absent row is dangerous, asserted rather than
    asserted-about. If this ever becomes uniform, the second test above
    is still correct but its rationale changes, and whoever reads it
    should find that out here rather than by reasoning about it."""
    text = TIERS.read_text()
    defaults = re.findall(r'default_model:\s*"([^"]+)"', text)
    assert len(set(defaults)) > 1, (
        "tier default models are now uniform; an unrouted call type no "
        "longer splits by tier, so update the reasoning above")


def test_shouldersurf_is_deliberately_left_alone():
    """SS is the other model: there the tier DOES buy a different model,
    and its rows differ on purpose. Stated here so nobody reads the TR
    rule as a global one and 'fixes' SS to match."""
    ss = json.loads(ROUTING.read_text())["apps"]["shouldersurf"]["call_types"]
    varied = [ct for ct, cfg in ss.items()
              if len(set((cfg.get("models") or {}).values())) > 1]
    assert varied, (
        "no SS call type varies by tier any more; if that was deliberate "
        "this test should go, but it is more likely someone applied the "
        "TR rule to the wrong app")
