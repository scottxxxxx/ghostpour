"""Prompt envelope composition spec, v2 (2026-08-03).

GP owns the recipe and the client executes it. See
docs/decisions/prompt-composition-doctrine.md and
docs/wire-contracts/prompt-envelope.md.

v1 asserted that `copilot_session` reproduced the shipped template
exactly, which was true of the template and false of the wire: the client
blanks summary and project in the system block and re-emits them in the
user turn. Implementing it as written would have moved two large blocks
into the system turn on the one surface the spec claimed to leave alone.

Two structural changes came out of that. Placement is now per surface
with a section-level default, because the same section genuinely lands in
different turns on different surfaces, and pretending otherwise is what
produced a spec that could not describe reality. And every surface
carries a verification flag, so the spec can never again claim to
describe the wire when it describes intent.
"""

import json
from pathlib import Path

import pytest

SPEC = json.loads(
    (Path(__file__).parent.parent / "config" / "remote"
     / "prompt-envelope.json").read_text())

SURFACES = SPEC["surfaces"]
SECTIONS = SPEC["sections"]
LANES = SPEC["lanes"]

# Anything that changes more often than per selection cannot sit inside a
# prefix we tell the client is byte-stable across turns.
_VOLATILITY = {
    "static": 0, "per_user": 1, "per_call_type": 1, "per_project": 2,
    "per_selection": 3, "periodic": 4, "per_turn": 5, "per_send": 5,
}

VERIFICATION_STATES = {
    "adopting_now", "unverified_describes_intent", "byte_diffed_against_wire",
}


# --- the doctrine ----------------------------------------------------


def test_instructions_are_editable_on_byok_and_nowhere_else():
    """The editability table. Their key, their model, their bill, so the
    CONTENTS become editable there. Nowhere else."""
    editable = {n for n, l in LANES.items() if l["instructions_editable"]}
    assert editable == {"byok"}


def test_an_unknown_model_still_resolves():
    """The requirement is that a model which did not exist when this file
    was written still gets a recipe, without an app update."""
    lanes = set(LANES)
    assert "default" in lanes, "removing the default lane breaks unknown models"
    res = SPEC["lane_resolution"]
    assert res["fallback"] in lanes
    for lane in res["by_api_format"].values():
        assert lane in lanes, f"lane_resolution points at unknown lane {lane}"


def test_lane_resolution_is_data_not_code():
    """Adding a lane must not need a client build, which means the mapping
    has to be served rather than compiled in."""
    res = SPEC["lane_resolution"]
    assert isinstance(res.get("by_api_format"), dict) and res["by_api_format"]
    assert isinstance(res.get("fallback"), str)


@pytest.mark.parametrize("lane", list(LANES))
def test_every_lane_names_an_instruction_variant(lane):
    assert LANES[lane]["instructions_variant"]


# --- structure -------------------------------------------------------


@pytest.mark.parametrize("surface", list(SURFACES))
def test_every_referenced_section_exists(surface):
    for key in ("system", "user"):
        for sec in SURFACES[surface][key]:
            assert sec in SECTIONS, f"{surface} references unknown section {sec}"


@pytest.mark.parametrize("surface", list(SURFACES))
def test_a_section_appears_once_per_surface(surface):
    """Placement varies BY surface, but within one surface a section is in
    exactly one turn. Both, or twice, is incoherent rather than flexible."""
    s = SURFACES[surface]
    placed = s["system"] + s["user"]
    assert len(placed) == len(set(placed)), f"{surface} places a section twice"


@pytest.mark.parametrize("surface", list(SURFACES))
def test_global_instructions_reach_every_surface(surface):
    """A standing rule that dies when the user leaves the live session is
    the complaint that started this."""
    assert "global_system_instructions" in SURFACES[surface]["system"]


@pytest.mark.parametrize("surface", list(SURFACES))
def test_gp_instruction_follows_user_instruction(surface):
    """Precedence is structural: GP's instruction is the more proximate one
    when the two disagree."""
    system = SURFACES[surface]["system"]
    assert (system.index("prompt_system_instructions")
            > system.index("global_system_instructions"))


@pytest.mark.parametrize("surface", list(SURFACES))
def test_user_query_is_last(surface):
    assert SURFACES[surface]["user"][-1] == "user_query"


@pytest.mark.parametrize("surface", list(SURFACES))
def test_growing_content_stays_out_of_the_system_block(surface):
    """transcript and conversation_history both grow every turn. In the
    system block they break the cached prefix on every send."""
    system = SURFACES[surface]["system"]
    for sec in ("transcript", "conversation_history"):
        assert sec not in system, f"{surface} puts {sec} in the system block"


@pytest.mark.parametrize("surface", list(SURFACES))
def test_cache_boundary_only_contains_stable_sections(surface):
    s = SURFACES[surface]
    boundary = s["cache_stable_prefix_ends_after"]
    assert boundary in s["system"], f"{surface} boundary not in its system list"
    overrides = s.get("stability_overrides", {})
    for sec in s["system"][:s["system"].index(boundary) + 1]:
        stability = overrides.get(sec, SECTIONS[sec]["stability"])
        assert _VOLATILITY[stability] <= 3, (
            f"{surface} claims {sec} ({stability}) is cache-stable")


@pytest.mark.parametrize("surface", list(SURFACES))
def test_stability_overrides_are_explained(surface):
    s = SURFACES[surface]
    if s.get("stability_overrides"):
        assert s.get("stability_note"), (
            f"{surface} overrides stability without saying why")


# --- honesty about what has been checked -----------------------------


@pytest.mark.parametrize("surface", list(SURFACES))
def test_every_surface_declares_whether_it_matches_the_wire(surface):
    """The v1 failure was a spec that described intent while claiming to
    describe current behavior. A surface that has not been byte-diffed
    must say so, in the file, where an implementer will see it."""
    s = SURFACES[surface]
    assert s["verification"] in VERIFICATION_STATES
    assert s.get("verification_note"), f"{surface} states no verification note"


def test_the_session_surfaces_are_still_flagged_unverified():
    """copilot_session and post_session_analysis were the ones v1 got
    wrong. They stay flagged until someone diffs them against the wire,
    and flipping the flag without doing that is the regression."""
    for surface in ("copilot_session", "post_session_analysis"):
        assert SURFACES[surface]["verification"] == "unverified_describes_intent"


def test_session_surfaces_put_summary_and_project_in_the_user_turn():
    """The actual correction. The client blanks both in the system block
    and re-emits them in the user turn, and in the system block they would
    bust the cached prefix every turn."""
    s = SURFACES["copilot_session"]
    for sec in ("rolling_summary", "project_context"):
        assert sec in s["user"], f"copilot_session must not put {sec} in system"
        assert sec not in s["system"]


def test_the_sections_the_client_already_sends_are_defined():
    """An absent section reads as undefined, not unchanged. Both of these
    ride the wire today."""
    for sec in ("conversation_history", "attached_photos_note"):
        assert sec in SECTIONS
        assert any(sec in s["user"] for s in SURFACES.values()), (
            f"{sec} is defined but placed on no surface")


def test_served_over_the_config_endpoint(client):
    r = client.get("/v1/config/prompt-envelope",
                   headers={"X-App-ID": "shouldersurf"})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] >= SPEC["version"]
    assert set(body["surfaces"]) == set(SURFACES)
    assert body["lanes"]["byok"]["instructions_editable"] is True
    assert body["lanes"]["managed"]["instructions_editable"] is False
    assert body["lanes"]["on_device"]["instructions_editable"] is False


# --- single owner of placement --------------------------------------


def test_the_envelope_declares_itself_the_placement_authority():
    """CQ: model-capabilities also carries a promptPlacement field, so two
    served configs can each assert placement. Additive-only means we can
    never delete the old one, which makes an explicit precedence rule
    mandatory rather than tidy-up: something has to say what wins for a
    build still reading the deprecated field."""
    auth = SPEC["placement_authority"]
    assert auth["owner"] == "prompt-envelope"
    assert "model-capabilities.models.*.promptPlacement" in auth["deprecated_elsewhere"]
    assert len(auth["note"]) > 80


def test_the_deprecated_field_is_still_present_and_inert():
    """Deprecated in place, not removed. If it ever disappears from
    model-capabilities we have broken every shipped build, and if its value
    ever varies someone has started using it again."""
    caps = json.loads(
        (Path(__file__).parent.parent / "config" / "remote"
         / "model-capabilities.json").read_text())["models"]
    values = {m.get("promptPlacement") for m in caps.values()}
    assert None not in values, "promptPlacement vanished; that breaks shipped builds"
    assert values == {"System"}, (
        f"promptPlacement now varies ({values}); it is deprecated and inert, "
        "so a varying value means something is reading it again")
