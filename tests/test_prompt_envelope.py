"""Prompt envelope composition spec (2026-08-02).

The envelope is GP's: which sections exist, their order, and whether each
lands in the system block or the user turn. SS composes to it and does not
edit it. These tests pin the invariants that make the spec meaningful, so
a later edit cannot quietly produce a document that says nothing.

See docs/wire-contracts/prompt-envelope.md.
"""

import json
from pathlib import Path

import pytest

SPEC = json.loads(
    (Path(__file__).parent.parent / "config" / "remote"
     / "prompt-envelope.json").read_text())

SURFACES = SPEC["surfaces"]
SECTIONS = SPEC["sections"]

# Volatile sections invalidate a cached prefix, so they may never be
# ordered ahead of stable ones inside the region we claim is cacheable.
_VOLATILITY = {
    "static": 0, "per_user": 1, "per_call_type": 1, "per_project": 2,
    "per_selection": 3, "periodic": 4, "per_turn": 5, "per_send": 5,
}


@pytest.mark.parametrize("surface", list(SURFACES))
def test_every_referenced_section_exists(surface):
    for key in ("system", "user"):
        for sec in SURFACES[surface][key]:
            assert sec in SECTIONS, f"{surface} references unknown section {sec}"


@pytest.mark.parametrize("surface", list(SURFACES))
def test_placement_is_global_not_per_surface(surface):
    """A section declared `system` must never be composed into the user
    turn on some other surface, or the vocabulary means nothing."""
    for key in ("system", "user"):
        for sec in SURFACES[surface][key]:
            assert SECTIONS[sec]["placement"] == key, (
                f"{surface} puts {sec} in {key} but it is declared "
                f"{SECTIONS[sec]['placement']}")


@pytest.mark.parametrize("surface", list(SURFACES))
def test_global_instructions_reach_every_surface(surface):
    """The whole point: a standing rule that dies when the user leaves the
    live session is the complaint that started this."""
    assert "global_system_instructions" in SURFACES[surface]["system"]


@pytest.mark.parametrize("surface", list(SURFACES))
def test_gp_instruction_follows_user_instruction(surface):
    """Precedence is structural. GP's instruction sits after the user's so
    it is the more proximate one when they disagree."""
    system = SURFACES[surface]["system"]
    assert (system.index("prompt_system_instructions")
            > system.index("global_system_instructions"))


@pytest.mark.parametrize("surface", list(SURFACES))
def test_user_query_is_last(surface):
    assert SURFACES[surface]["user"][-1] == "user_query"


@pytest.mark.parametrize("surface", list(SURFACES))
def test_growing_content_stays_out_of_the_system_block(surface):
    """transcript grows every turn; in the system block it would break the
    cached prefix on every send."""
    assert "transcript" not in SURFACES[surface]["system"]


@pytest.mark.parametrize("surface", list(SURFACES))
def test_cache_boundary_is_real_and_stable(surface):
    """Everything up to the declared boundary must be stable across turns,
    otherwise the boundary is a claim we cannot keep."""
    s = SURFACES[surface]
    boundary = s["cache_stable_prefix_ends_after"]
    assert boundary in s["system"], f"{surface} boundary not in its system list"
    overrides = s.get("stability_overrides", {})
    prefix = s["system"][:s["system"].index(boundary) + 1]
    for sec in prefix:
        stability = overrides.get(sec, SECTIONS[sec]["stability"])
        assert _VOLATILITY[stability] <= 3, (
            f"{surface} claims {sec} ({stability}) is in the cache-stable "
            "prefix")


@pytest.mark.parametrize("surface", list(SURFACES))
def test_stability_overrides_are_explained_and_only_loosen(surface):
    """An override says a surface freezes something the section is normally
    allowed to change. It must name a real section and carry the reason,
    so nobody later reads it as a way to silence the boundary check."""
    s = SURFACES[surface]
    overrides = s.get("stability_overrides", {})
    if not overrides:
        return
    assert s.get("note"), f"{surface} overrides stability without explaining it"
    for sec, stability in overrides.items():
        assert sec in SECTIONS, f"{surface} overrides unknown section {sec}"
        assert stability in _VOLATILITY, f"{surface} bad stability {stability}"
        assert sec in s["system"] or sec in s["user"], (
            f"{surface} overrides {sec} but never composes it")


def test_only_global_instructions_are_ever_user_editable():
    """The envelope is GP's on every lane. Contents are editable in exactly
    one section, and only on the user's own key."""
    editable = {k: v["editable_on"] for k, v in SECTIONS.items() if v["editable_on"]}
    assert editable == {"global_system_instructions": ["byok"]}


def test_call_types_are_not_claimed_by_two_surfaces():
    seen = {}
    for name, s in SURFACES.items():
        for ct in s["call_types"]:
            assert ct not in seen, f"{ct} claimed by {seen.get(ct)} and {name}"
            seen[ct] = name


def test_both_chat_surfaces_are_covered():
    """The two surfaces that had no global instructions at all."""
    covered = {ct for s in SURFACES.values() for ct in s["call_types"]}
    assert {"meeting_chat", "meeting_chat_follow_up",
            "project_chat", "project_chat_follow_up"} <= covered


def test_served_over_the_config_endpoint(client):
    """SS has to be able to fetch it. A new config name is invisible to
    older builds, which never request it, so this is additive by
    construction."""
    r = client.get("/v1/config/prompt-envelope",
                   headers={"X-App-ID": "shouldersurf"})
    assert r.status_code == 200
    body = r.json()
    # Version is checked as a floor, not an equality: the persisted overlay
    # legitimately runs ahead of the bundled file once hydration folds in a
    # bundle addition and bumps it so clients refetch.
    assert body["version"] >= SPEC["version"]
    assert set(body["surfaces"]) == set(SPEC["surfaces"])
    for name, surface in body["surfaces"].items():
        assert "global_system_instructions" in surface["system"], name
        assert surface["user"][-1] == "user_query", name
