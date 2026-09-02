"""People is exempt from the free-tier Memory cap (2026-08-10, Scott).

People is enabled on every tier and the only closed gate is signed-out.
But People is BUILT from captured meetings, and a free user was capped at
one capture a month, of which CQ extracts people from roughly 43%. So a
free user's People tab was empty or near-empty for months, which reads as
broken rather than as locked. That is the one impression a gate must never
give: a locked feature should look locked.

The fix rests on one observation. A capture feeds TWO things and only one
of them is paid: person entities are People, which is free everywhere, and
quilt patches are Memory, which is not. Skipping the capture starved a
feature the user is entitled to in order to meter one they are not.
"""

import json
import pathlib

import pytest

from app.services.memory_capture_policy import resolve_memory_capture_verdict as verdict

LOCALES = ("", ".es", ".ja", ".fr")


# --- the ruling -------------------------------------------------------


def test_a_free_user_out_of_memory_quota_still_captures():
    """The whole change. Before, this returned skip_with_cta, so the
    meeting never reached CQ and People never learned anyone from it."""
    v = verdict(feature_state="disabled", has_quota=False, people_enabled=True)
    assert v.verdict == "capture_with_cta"


def test_the_first_capture_of_the_month_still_reads_as_the_free_one():
    """The upsell keeps its rhythm. Quota still governs the CTA even though
    it no longer governs whether we capture."""
    v = verdict(feature_state="disabled", has_quota=True, people_enabled=True)
    assert v.cta_kind == "free_within_quota_footer"


def test_past_quota_says_what_is_being_built_not_that_nothing_is():
    """The old copy asked "Want your AI to remember meetings?", which is now
    false: it IS remembering, they just cannot read it back."""
    v = verdict(feature_state="disabled", has_quota=False, people_enabled=True)
    assert v.cta_kind == "free_people_only"


# --- the gate still has to be a gate ----------------------------------


def test_turning_people_off_closes_the_door_again():
    """The exemption is keyed to the People entitlement, read from the same
    matrix as every other gate. Flipping that dashboard row has to actually
    stop the captures, or the toggle is decorative."""
    v = verdict(feature_state="disabled", has_quota=False, people_enabled=False)
    assert v.verdict == "skip_with_cta"
    assert v.cta_kind == "free_no_quota_only"


def test_paid_tiers_are_untouched():
    """Pro captures fully and Plus is unchanged. This was only ever a free
    tier problem and the change must not reach anyone else."""
    assert verdict(feature_state="enabled", has_quota=False,
                   people_enabled=True).verdict == "capture"
    assert verdict(feature_state="enabled", has_quota=False,
                   people_enabled=True).cta_kind is None
    # teaser is Free since 2026-08-24: same rules as disabled, never recall_only.
    assert verdict(feature_state="teaser", has_quota=False, people_enabled=True).verdict == \
        verdict(feature_state="disabled", has_quota=False, people_enabled=True).verdict


def test_the_people_toggle_is_not_inert():
    """Not hardcoded True. If someone assumed it, the dashboard toggle would
    silently stop meaning anything.

    REWRITTEN 2026-09-02. It used to grep cq_proxy.py for the literal
    `"people")` and it broke the moment that call gained an app-id argument,
    which is a formatting change and not a behavioural one. A source-text
    assertion cannot tell those apart, and would equally have gone green on a
    file carrying that string inside a comment while the gate read True.

    What this can prove on its own is that the verdict FOLLOWS the flag, so a
    matrix flip has somewhere to land. That the call site actually consults
    the resolver, and now passes the calling app, is proved on the wire by
    tests/test_people_proxy.py, whose stub asserts it receives an app id.
    """
    on = verdict(feature_state="disabled", has_quota=False, people_enabled=True)
    off = verdict(feature_state="disabled", has_quota=False, people_enabled=False)
    assert on.verdict != off.verdict, (
        "people_enabled does not change the verdict, so the matrix row is inert")


# --- the copy ---------------------------------------------------------


@pytest.mark.parametrize("loc", LOCALES)
def test_the_new_cta_ships_in_every_locale(loc):
    p = pathlib.Path(f"config/remote/tiers{loc}.json")
    if not p.exists():
        pytest.skip("not shipped")
    cta = json.loads(p.read_text())["feature_definitions"]["context_quilt"]["cta_strings"]
    assert cta.get("free_people_only", "").strip()


@pytest.mark.parametrize("loc", LOCALES)
def test_the_copy_states_what_the_system_does(loc):
    """House rule: served strings say what the product IS doing, never what
    the plan withholds. "Your AI is building your People list" is true and
    is the reason the meeting was worth capturing."""
    p = pathlib.Path(f"config/remote/tiers{loc}.json")
    if not p.exists():
        pytest.skip("not shipped")
    s = json.loads(p.read_text())["feature_definitions"]["context_quilt"]["cta_strings"]["free_people_only"]
    for dash in ("—", "–"):
        assert dash not in s
    assert "People" in s or "Personas" in s or "People" in s or "Personnes" in s or "People" in s
