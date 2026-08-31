"""The inventory generator must never fail QUIETLY.

An inventory is only ever consumed by diffing it against another team's.
That gives the failure mode its shape: **an empty or truncated inventory
diffs clean against everything**, and a clean diff is exactly the answer
everybody is hoping for. So the way this tool breaks is by reporting
agreement, on both sides, forever.

That is not hypothetical here. The first run of the generator raised
ModuleNotFoundError and produced zero names. Loud in a terminal; silent the
moment anyone wraps it in a cron, a Makefile, or a `|| true`.

These tests exist so the generator cannot go quiet: it must run, it must
find routes, and it must still contain the exact names that the three known
instances of this bug travelled on.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.field_inventory import build, flat_names

REPO = Path(__file__).resolve().parent.parent


def test_inventory_is_not_empty():
    """The failure that would read as 'both teams agree'."""
    inv = build()
    assert inv["routes"], "no routes discovered; a diff against this is vacuous"
    assert len(flat_names(inv)) > 10, \
        "suspiciously few names; a truncated inventory diffs clean and lies"


@pytest.mark.parametrize("name", [
    # Every field one of the three instances actually travelled on. If a
    # refactor drops one from the inventory, the instrument stops covering
    # the bug it was built for, and does so without any test going red
    # elsewhere.
    "to_name",         # instance 1, silent, found by a three-way audit
    "project_name",    # instance 2, loud, CQ 422'd naming it
    "client_id",       # instance 3, silent, 200s and rendered rows
    "deadline_date",   # instance 3
    "patch_type",      # the audit find: dropped on update, every edit a no-op
    "label",           # the dormant trap: CQ takes `label`, SS encodes
                       # `relationship`. GP's side is correct; the DIFF is
                       # what surfaces the disagreement, which is the whole
                       # point of generating rather than documenting.
])
def test_known_incident_fields_are_covered(name):
    assert name in flat_names(build())


def test_generator_runs_as_a_script_from_the_repo_root():
    """Import-time success is not the same as running.

    The first version imported fine under pytest (which puts the repo on
    sys.path for free) and died as a script. Anything that consumes this
    runs it as a script, so test it that way.
    """
    proc = subprocess.run(
        [sys.executable, "scripts/field_inventory.py", "--names"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"generator failed as a script:\n{proc.stderr[-2000:]}"
    names = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(names) > 10, f"script produced {len(names)} names"
    assert "patch_type" in names


def test_extra_posture_is_recorded_per_model():
    """`extra` is why instance 3 was silent, so it belongs IN the inventory.

    A reader diffing two inventories needs to know whether a model that is
    missing a name would have DROPPED it or merely not modelled it. Those
    are different bugs with the same diff line.
    """
    inv = build()
    postures = {node["extra"] for node in inv["routes"].values()}
    assert postures, "no models described"
    assert postures <= {"allow", "ignore", "forbid"}
    # #824 set every proxy body model to allow. If one regresses to ignore,
    # the dropping half is back and this is where it shows.
    assert postures == {"allow"}, f"a proxy body model can drop keys again: {postures}"


def test_hand_built_handlers_are_declared_not_omitted():
    """The gap that made `model_config == "allow"` a green lie.

    Both assign-project handlers built their payload by hand, so a
    model-derived inventory cannot see their names. Silently omitting them
    would make the inventory claim coverage it does not have."""
    inv = build()
    assert "hand_built" in inv
    assert "caveat" in inv and "by hand" in inv["caveat"]
