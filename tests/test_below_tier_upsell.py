"""Detecting a file ask at every tier, and selling the upgrade.

Scott's ruling 2026-08-15: there is no gate at the PLAN level on
detection. A shape gate applied identically to every tier honours that;
a tier gate would not.

The number that forced this: the old vocabulary prefilter matched FIVE
of 216 real artifact asks generated blind to it. It was suppressing file
intent at every tier, paying ones included, for anyone who did not
happen to type a file noun, so the upsell reached almost nobody it was
written for. Widening it to include request shape takes that to 56% with
zero trips across a 40 turn ordinary-chat control.

The rest are asks like "who decided what and why lol just that", which
read as questions. Those get answered in chat, where the existing teaser
CTA offers the file, and that is the better product answer than assuming
somebody wanted a download.
"""

from __future__ import annotations

import pytest

from app.services.document_generation import (
    _UPSELL_DEFAULTS,
    inline_artifact_guidance,
    looks_like_file_ask,
    upsell_line,
)


@pytest.mark.parametrize("text", [
    "give me the action items with owners",
    "can you put together a risk register",
    "i need a breakdown of the costs",
    "show me the open questions",
    "pull together what we decided",
    "make me a comparison table",
    "dame la lista de acciones",
    "fais-moi un tableau des risques",
])
def test_request_shape_reaches_the_classifier(text: str) -> None:
    """None of these say "file", "spreadsheet" or "document"."""
    assert looks_like_file_ask(text), text


@pytest.mark.parametrize("text", [
    "what did Mike say about the refill flow",
    "who was on the call",
    "thanks that helps",
    "ok got it",
    "what does FDE mean",
    "who owns the auth fix?",
    "what time is the standup tomorrow?",
    "when is the next one",
    "hmm interesting",
])
def test_ordinary_chat_still_costs_nothing(text: str) -> None:
    """The gate exists to keep the classifier off ordinary turns. A trip
    here is 1.2 seconds of latency the user did not ask for."""
    assert not looks_like_file_ask(text), text


def test_the_upsell_ships_on() -> None:
    """It shipped disabled, so the detection work reached nobody."""
    assert _UPSELL_DEFAULTS["enabled"] is True
    assert _UPSELL_DEFAULTS["text"]


def test_the_default_copy_obeys_the_house_rule() -> None:
    for value in _UPSELL_DEFAULTS.values():
        if isinstance(value, str):
            assert "—" not in value and "–" not in value


def test_the_line_names_the_artifact_they_asked_for() -> None:
    """"I could build you that risk register" is a different sentence
    from "I could generate a file"."""
    out = upsell_line(_UPSELL_DEFAULTS, "Pro", "risk_register")
    assert "risk register" in out
    assert "Pro" in out


def test_an_unresolved_artifact_never_leaves_a_placeholder_showing() -> None:
    """Null is the common case, not the exception: the classifier returns
    it for anything outside the catalog."""
    out = upsell_line(_UPSELL_DEFAULTS, "Pro", None)
    assert "{artifact}" not in out and "{tier}" not in out
    assert out.strip()


def test_an_unknown_artifact_key_degrades_to_the_generic_noun() -> None:
    out = upsell_line(_UPSELL_DEFAULTS, "Pro", "not_a_contract")
    assert "{artifact}" not in out
    assert _UPSELL_DEFAULTS["generic_artifact"] in out


def test_empty_served_text_produces_no_line_rather_than_a_stub() -> None:
    assert upsell_line({"enabled": True, "text": ""}, "Pro", None) == ""


def test_the_tier_name_is_never_hardcoded() -> None:
    """Scott's standing requirement: never assume the feature is Pro."""
    assert "Pro" not in _UPSELL_DEFAULTS["text"]
    assert "{tier}" in _UPSELL_DEFAULTS["text"]
    assert "Plus" in upsell_line(_UPSELL_DEFAULTS, "Plus", None)


def test_a_below_tier_user_gets_the_content_not_just_a_pitch() -> None:
    """Scott 2026-08-15: give them the closest thing we can, knowing we
    cannot build the file. When we know which artifact they wanted we
    also know its columns, so the same content arrives as a table they
    could paste into a sheet themselves. The upgrade then buys the FILE
    rather than the information."""
    from app.services.artifact_types import CONTRACTS

    g = inline_artifact_guidance("action_register")
    assert "markdown table" in g
    for col in CONTRACTS["action_register"].columns:
        assert col.label in g, col.label
    assert "complete enough to use as it stands" in g


def test_guidance_survives_an_unresolved_artifact() -> None:
    g = inline_artifact_guidance(None)
    assert "table" in g and g.strip()
    assert inline_artifact_guidance("not_a_contract") == ""


def test_the_teaser_sells_the_file_not_the_information() -> None:
    """The copy must not imply we are withholding the answer."""
    text = _UPSELL_DEFAULTS["text"]
    assert "file" in text.lower()
    for withholding in ("cannot", "can't", "unable", "not available",
                        "upgrade to see", "unlock"):
        assert withholding not in text.lower(), withholding
