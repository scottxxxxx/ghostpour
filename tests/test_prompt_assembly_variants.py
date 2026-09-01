"""Jurisdiction variants and declared prompt variables (2026-09-01).

Scott's ask: one call_type must be able to serve a different prompt by where
the user is, while the app keeps calling one endpoint, and he must be able to
see and edit those variants.

Both are deliberately the SAME mechanism `modes` already uses: overrides
inside one document. One document is what makes the dashboard show every
variant side by side, and it is what stops a file per state per language
from drifting apart silently.

The second half here is a fail-closed change. Before it, a config whose
template named a placeholder the caller did not supply produced a log line
and a prompt containing the literal text "{{known_facts}}", which reaches a
model as a perfectly well-formed request and is not one.
"""

from __future__ import annotations

import pytest

from app.services.prompt_assembly import (
    MissingPromptVariables,
    _CALL_TYPE_TO_CONFIG,
    assemble_prompt,
)

_SLUG = "n400/interview-turn"
_CALL = "n400_interview_turn"


def _cfg(**over) -> dict:
    base = {
        "version": 1,
        "systemPrompt": "BASE SYSTEM",
        "userPromptTemplate": "Q: {{question_text}}\nA: {{user_input}}",
        "requiredVariables": ["question_text"],
    }
    base.update(over)
    return {_SLUG: base}


_VARS = {"question_text": "When were you born?"}


# --- the base path still works ----------------------------------------------

def test_no_jurisdiction_gets_the_base_prompt():
    out = assemble_prompt(_CALL, "April 12th, 1987", _cfg(), variables=_VARS)
    assert out["system_prompt"] == "BASE SYSTEM"
    assert "When were you born?" in out["user_content"]
    assert "April 12th, 1987" in out["user_content"]


def test_a_config_with_no_jurisdictions_map_is_untouched_by_one():
    out = assemble_prompt(_CALL, "x", _cfg(), jurisdiction="US-TX", variables=_VARS)
    assert out["system_prompt"] == "BASE SYSTEM"


# --- the variant axis -------------------------------------------------------

def test_a_matching_jurisdiction_overrides_the_prompt():
    configs = _cfg(jurisdictions={"US-CA": {"systemPrompt": "CALIFORNIA SYSTEM"}})
    out = assemble_prompt(_CALL, "x", configs, jurisdiction="US-CA", variables=_VARS)
    assert out["system_prompt"] == "CALIFORNIA SYSTEM"


def test_an_unlisted_jurisdiction_inherits_the_base_rather_than_getting_nothing():
    """The safe direction. A location we have not written a variant for gets
    the general prompt; it must never fall through to an empty prompt, which
    is a model answering immigration questions uninstructed."""
    configs = _cfg(jurisdictions={"US-CA": {"systemPrompt": "CALIFORNIA SYSTEM"}})
    out = assemble_prompt(_CALL, "x", configs, jurisdiction="US-NY", variables=_VARS)
    assert out["system_prompt"] == "BASE SYSTEM"


def test_a_variant_overrides_only_what_it_names():
    """Partial override, like modes. A variant that changed the system prompt
    must not silently drop maxTokens or the user template with it."""
    configs = _cfg(maxTokens=2048,
                   jurisdictions={"US-CA": {"systemPrompt": "CALIFORNIA SYSTEM"}})
    out = assemble_prompt(_CALL, "x", configs, jurisdiction="US-CA", variables=_VARS)
    assert out["system_prompt"] == "CALIFORNIA SYSTEM"
    assert out["max_tokens"] == 2048
    assert "When were you born?" in out["user_content"]


def test_jurisdiction_wins_over_mode():
    """Order is modes then jurisdictions, and it is not arbitrary: the mode
    says what is being asked, the jurisdiction says what we are allowed to
    say there. The second is the one that must not be overridable."""
    configs = _cfg(
        modes={"Interview": {"systemPrompt": "MODE SYSTEM"}},
        jurisdictions={"US-CA": {"systemPrompt": "CALIFORNIA SYSTEM"}},
    )
    out = assemble_prompt(_CALL, "x", configs, prompt_mode="Interview",
                          jurisdiction="US-CA", variables=_VARS)
    assert out["system_prompt"] == "CALIFORNIA SYSTEM"


def test_a_mode_still_applies_where_no_variant_exists():
    configs = _cfg(
        modes={"Interview": {"systemPrompt": "MODE SYSTEM"}},
        jurisdictions={"US-CA": {"systemPrompt": "CALIFORNIA SYSTEM"}},
    )
    out = assemble_prompt(_CALL, "x", configs, prompt_mode="Interview",
                          jurisdiction="US-NY", variables=_VARS)
    assert out["system_prompt"] == "MODE SYSTEM"


@pytest.mark.parametrize("juris", [None, "", "   "])
def test_a_blank_jurisdiction_is_not_a_lookup(juris):
    configs = _cfg(jurisdictions={"": {"systemPrompt": "EMPTY KEY SYSTEM"}})
    out = assemble_prompt(_CALL, "x", configs, jurisdiction=juris, variables=_VARS)
    assert out["system_prompt"] == "BASE SYSTEM"


# --- declared variables, and failing closed ---------------------------------

def test_named_variables_are_substituted_by_name():
    configs = _cfg(userPromptTemplate="[{{a}}][{{b}}]", requiredVariables=[])
    out = assemble_prompt(_CALL, "x", configs, variables={"a": "one", "b": "two"})
    assert out["user_content"] == "[one][two]"


def test_a_missing_required_variable_refuses_the_turn():
    with pytest.raises(MissingPromptVariables) as exc:
        assemble_prompt(_CALL, "x", _cfg(), variables={})
    assert exc.value.missing == ["question_text"]


@pytest.mark.parametrize("value", ["", "   ", None])
def test_present_but_blank_counts_as_missing(value):
    """A blank known_facts is not 'nothing is known', it is a client that
    forgot to send it, and the two produce different interviews."""
    with pytest.raises(MissingPromptVariables):
        assemble_prompt(_CALL, "x", _cfg(), variables={"question_text": value})


def test_a_config_that_declares_nothing_keeps_the_old_warn_and_send_behaviour():
    """Every prompt that predates this change must be untouched. TR's configs
    declare no requiredVariables, so an unreplaced placeholder still warns
    and sends rather than raising."""
    configs = _cfg(userPromptTemplate="[{{never_supplied}}]", requiredVariables=[])
    out = assemble_prompt(_CALL, "x", configs, variables={})
    assert out["user_content"] == "[{{never_supplied}}]"


def test_the_legacy_payload_names_still_mean_the_payload():
    for legacy in ("job_description", "resume_text", "user_input"):
        configs = _cfg(userPromptTemplate="<%s>" % ("{{%s}}" % legacy),
                       requiredVariables=[])
        out = assemble_prompt(_CALL, "PAYLOAD", configs, variables={})
        assert out["user_content"] == "<PAYLOAD>"


def test_an_extra_variable_nobody_named_changes_nothing():
    """A config takes what it declares. Adding a metadata key must never
    alter a prompt that does not name it."""
    configs = _cfg(requiredVariables=[])
    out = assemble_prompt(_CALL, "x", configs,
                          variables={"question_text": "Q", "unused_key": "ZZZ"})
    assert "ZZZ" not in out["user_content"]


# --- through the route ------------------------------------------------------

def test_the_registered_slug_is_the_one_that_ships():
    assert _CALL_TYPE_TO_CONFIG[_CALL] == _SLUG


def test_a_turn_missing_metadata_is_422_not_a_silent_send(client, free_user):
    """The whole point of failing closed, exercised on the wire."""
    from tests.conftest import chat_request

    # system_prompt MUST be empty: server-side assembly only runs when the
    # client sends none, and conftest's helper supplies one by default. An
    # earlier version of this test passed a prompt and got a happy 200 while
    # asserting nothing about assembly at all.
    body = chat_request(system_prompt="", user_content="April 12th, 1987")
    body["metadata"] = {"call_type": _CALL}          # no required variables
    r = client.post("/v1/chat", json=body,
                    headers={**free_user["headers"], "X-App-ID": "n400"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "missing_prompt_variables"
    assert "known_facts" in detail["details"]["missing"]


# --- optional variables -----------------------------------------------------

def test_a_declared_optional_variable_that_is_absent_resolves_to_nothing():
    """N-400 sends section_end_instruction only at a section boundary, which
    is most turns without it. Before this, the placeholder survived into the
    prompt as the literal text "{{section_end_instruction}}" and the model
    read it as content. Found by N-400 telling us they send it ABSENT rather
    than empty, not by reading the code."""
    configs = _cfg(userPromptTemplate="A{{opt}}B", requiredVariables=[],
                   optionalVariables=["opt"])
    out = assemble_prompt(_CALL, "x", configs, variables={})
    assert out["user_content"] == "AB"


def test_an_optional_variable_that_is_sent_is_still_substituted():
    configs = _cfg(userPromptTemplate="A{{opt}}B", requiredVariables=[],
                   optionalVariables=["opt"])
    out = assemble_prompt(_CALL, "x", configs, variables={"opt": "MIDDLE"})
    assert out["user_content"] == "AMIDDLEB"


def test_an_undeclared_leftover_is_not_silently_swallowed():
    """Blanking anything unrecognised would hide a typo in the template. Only
    declared names are blanked; the rest stay visible and warn."""
    configs = _cfg(userPromptTemplate="A{{typoo}}B", requiredVariables=[],
                   optionalVariables=["opt"])
    out = assemble_prompt(_CALL, "x", configs, variables={})
    assert out["user_content"] == "A{{typoo}}B"


def test_the_shipped_config_leaves_no_literal_braces_on_a_mid_section_turn():
    """End to end against the REAL config: send the eight required keys and
    omit the optional one, exactly as N-400's client does, and assert the
    assembled prompt carries no unsubstituted placeholder."""
    import json as _json
    import re as _re
    from pathlib import Path as _Path

    cfg = _json.loads((_Path(__file__).parent.parent / "config" / "remote"
                       / "n400" / "interview-turn.json").read_text())
    supplied = {name: f"<{name}>" for name in cfg["requiredVariables"]}
    out = assemble_prompt(_CALL, "April 12th, 1987", {_SLUG: cfg},
                          variables=supplied)
    assert not _re.search(r"\{\{\w+\}\}", out["user_content"]), out["user_content"]
