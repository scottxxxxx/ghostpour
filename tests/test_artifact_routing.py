"""Which lane builds the file, and what we ask when we cannot tell.

The gate order is the substance here. Computation is checked before
artifact matching because it is cheap to decide and expensive to get
wrong: a sandbox that sums a column is correct, and a model that sums it
in its head is unverifiable. Everything uncertain degrades to the
provider lane, which is what we already ship today, so a routing mistake
costs money rather than correctness.

Scoring replaces `doc_templates.match_template`'s first-match-wins for
one reason: with two registry entries dict order is invisible, and with
ten it silently decides which file the user gets.
"""

from __future__ import annotations

import pytest

from app.services.artifact_routing import (
    question_for,
    reroute_on_model_signal,
    requires_provider,
    route,
    score_contracts,
)
from app.services.artifact_types import CONTRACTS


@pytest.mark.parametrize("text,expected", [
    ("make me a risk register from this", "risk_register"),
    ("can you put together a test plan", "test_plan"),
    ("I need a decision log for the project", "decision_log"),
    ("give me the action items with owners", "action_register"),
    ("what are the open questions", "open_questions"),
    ("build a requirements matrix", "requirements"),
    ("a cost estimate for this work", "budget"),
    ("compare the options we discussed", "option_comparison"),
    ("what keeps coming up across meetings", "topic_tracker"),
])
def test_a_named_artifact_routes_to_its_contract(text, expected) -> None:
    r = route(text, fmt="xlsx")
    assert r.lane == "contract", r
    assert r.contract == expected, r.scores


def test_an_unnamed_tabular_ask_uses_the_generic_plan_lane() -> None:
    """Still ours, still cheaper than the sandbox, just uncontracted."""
    r = route("can you make me a spreadsheet of what we covered",
              fmt="xlsx")
    assert r.lane == "plan"
    assert r.reason == "tabular_no_contract"


def test_an_unmatched_non_tabular_ask_goes_to_the_provider() -> None:
    r = route("write up a one page summary of the meeting")
    assert r.lane == "provider"
    assert r.reason == "no_artifact_match"


@pytest.mark.parametrize("fmt", ["docx", "pptx", "pdf"])
def test_formats_we_cannot_render_are_the_providers(fmt) -> None:
    """We have one renderer. This is coverage, not a capability gap, but
    it is a real gap today and must not route to us."""
    r = route("make me a risk register", fmt=fmt)
    assert r.lane == "provider"
    assert r.reason == "format_not_renderable"


def test_computation_over_an_attachment_beats_a_contract_match() -> None:
    """Gate order is the whole point: this names an artifact we own AND
    needs arithmetic we cannot verify. The arithmetic wins."""
    r = route("pivot this by region and give me the cost estimate",
              fmt="xlsx", has_attachment=True)
    assert r.lane == "provider"
    assert r.reason == "computation_over_attachment"


def test_a_transform_verb_without_an_attachment_is_not_computation() -> None:
    """'chart' lives inside 'gantt chart'. Without a file there is
    nothing to compute over, so this must not divert."""
    assert requires_provider("make me a gantt chart", has_attachment=False) is None
    assert requires_provider("chart our progress", has_attachment=False) is None


def test_generated_volume_goes_to_the_provider_with_or_without_a_file() -> None:
    """Structured output must emit every row in tokens; a loop does not."""
    assert requires_provider("test cases for every combination of plan "
                             "type and state") == "generated_volume"
    assert requires_provider("generate 500 scenarios") == "generated_volume"


def test_a_close_second_becomes_a_question_not_a_coin_flip() -> None:
    r = route("give me a log of the actions and the decisions", fmt="xlsx")
    assert r.reason == "ambiguous_artifact"
    assert r.needs_question
    assert set(r.candidates) >= {"action_register", "decision_log"}


def test_the_question_names_what_they_get_not_the_internal_type() -> None:
    r = route("give me a log of the actions and the decisions", fmt="xlsx")
    q = question_for(r)
    assert "action_register" not in q and "decision_log" not in q
    assert "owner" in q or "commitment" in q
    assert q.endswith("Which would help?")


def test_a_question_never_becomes_a_menu() -> None:
    """A nine item list is a form. Cap the options at three."""
    r = route("give me the actions, decisions, risks, open questions, "
              "requirements and budget", fmt="xlsx")
    if r.needs_question:
        assert len(r.candidates) <= 3


def test_a_bare_topic_word_does_not_outscore_a_named_artifact() -> None:
    """'risks' appears in any meeting that worried about something."""
    scores = score_contracts("we talked about risks, but build me a "
                             "test plan")
    assert scores["test_plan"] > scores.get("risk_register", 0)


def test_the_model_can_still_send_it_back_after_we_routed_to_ourselves() -> None:
    """The only gate that sees content rather than the request."""
    assert reroute_on_model_signal({"needs_computation": False}) is None
    back = reroute_on_model_signal({"needs_computation": True})
    assert back is not None and back.lane == "provider"
    assert back.reason == "model_declared_computation"


def test_every_contract_is_reachable_by_its_own_name() -> None:
    """A contract nothing can route to is dead code."""
    for name, c in CONTRACTS.items():
        assert c.hints, f"{name} has no hints"
        assert c.offer_noun, f"{name} has no offer noun"
        strongest = max(c.hints, key=lambda h: h[1])[0]
        r = route(f"please build a {strongest}", fmt="xlsx")
        assert r.lane == "contract"
        assert r.contract == name or name in r.candidates, (
            f"{name} unreachable via {strongest!r}: {r.scores}")


def test_hints_are_not_english_only() -> None:
    """The existing registry carries Spanish and Japanese and French
    shipped across all seven surfaces. English-only hints work for some
    users and silently fail for others."""
    for name, c in CONTRACTS.items():
        non_ascii_or_latin = [h for h, _ in c.hints
                              if not h.isascii() or " de " in h
                              or " des " in h or "d'" in h]
        assert non_ascii_or_latin, f"{name} has no non-English hints"


def test_reason_is_a_stable_token_for_telemetry() -> None:
    """These land in logs and become the answer to which artifacts get
    used, so they must be enumerable rather than prose."""
    known = {"generated_volume", "computation_over_attachment",
             "format_not_renderable", "tabular_no_contract",
             "no_artifact_match", "contract_match", "ambiguous_artifact",
             "model_declared_computation", "existing_template",
             "ambiguous_plan_version"}
    samples = [
        route("make me a risk register", fmt="xlsx"),
        route("write a summary"),
        route("make a spreadsheet", fmt="xlsx"),
        route("pivot this", fmt="xlsx", has_attachment=True),
        route("a risk register", fmt="pdf"),
        route("generate 500 scenarios"),
        route("make me a gantt chart", fmt="xlsx"),
        route("can you build a project plan", fmt="xlsx"),
    ]
    for r in samples:
        assert r.reason in known, r.reason
        assert " " not in r.reason


# --- Regressions found by probing, not by design. Each of these passed
# --- the original suite because the tests matched the implementation.

def test_a_gantt_ask_stays_with_the_gantt_registry() -> None:
    """First probe caught "make me a gantt chart" falling to the generic
    plan lane, which would have quietly regressed a shipped feature that
    builds a far better artifact. Still our renderer, so it is a lane of
    its own rather than a handoff."""
    r = route("make me a gantt chart", fmt="xlsx")
    assert r.lane == "template"
    assert r.reason == "existing_template"
    assert r.contract == "gantt_smartsheet"


def test_an_ambiguous_plan_ask_keeps_its_own_question() -> None:
    """Scott's 2026-08-11 ruling: plan-ish asks that match no template
    hint get one question about which version. The contract router must
    not swallow that."""
    for text in ("can you build a project plan",
                 "a detailed project plan with the progress curve"):
        r = route(text, fmt="xlsx")
        assert r.lane == "template", text
        assert r.reason == "ambiguous_plan_version", text


def test_restating_supplied_data_counts_as_computation() -> None:
    """"turn the attached csv into a summary table" leaked to our lane on
    the first probe. Aggregating supplied rows IS arithmetic, and the
    model would have done it in its head."""
    for text in ("turn the attached csv into a summary table",
                 "summarize this spreadsheet by owner"):
        r = route(text, fmt="xlsx", has_attachment=True)
        assert r.lane == "provider", text
        assert r.reason == "computation_over_attachment", text


def test_an_empty_ask_never_reaches_a_build_lane() -> None:
    """A stated xlsx format alone was enough to route empty text to the
    plan lane."""
    for text in ("", "   ", None):
        r = route(text, fmt="xlsx")
        assert r.lane == "provider"


def test_an_unmatched_tabular_ask_degrades_to_ours_not_theirs() -> None:
    """The safe failure mode: a conversational ask we cannot classify
    still gets a file from our cheaper lane rather than a wrong
    specialized artifact."""
    for text in ("what do we need to do",
                 "a list of everything Suresh owes us"):
        r = route(text, fmt="xlsx")
        assert r.lane == "plan", text


# --- The classifier stage. Measured 2026-08-15 on utterances generated
# --- blind to our hint vocabulary: lexical alone got 21% acceptable and
# --- missed 73%. With the classifier, 100% on the tuning set and 98% on
# --- a held-out set with zero wrong artifacts.

def test_the_classifier_catalog_is_built_from_the_registry() -> None:
    """A new contract must teach the classifier about itself, or the
    prompt rots in a different file from the thing it describes."""
    from app.services.artifact_routing import artifact_classifier_system
    sysprompt = artifact_classifier_system()
    for name, c in CONTRACTS.items():
        assert f'"{name}"' in sysprompt, name
        assert c.offer_noun.split("(")[0].strip()[:20] in sysprompt


def test_boundary_notes_reach_the_classifier_not_the_user() -> None:
    """Telling a user "not to be confused with a test plan" is noise;
    the classifier needs exactly that. These pairs actually collided."""
    from app.services.artifact_routing import artifact_classifier_system
    sysprompt = artifact_classifier_system()
    assert "Boundary:" in sysprompt
    for name in ("requirements", "test_plan", "open_questions",
                 "action_register"):
        assert CONTRACTS[name].classifier_note, name
        assert CONTRACTS[name].classifier_note not in CONTRACTS[name].offer_noun


def test_the_classifier_reads_paraphrase_the_hints_cannot() -> None:
    """"who's on the hook for stuff" shares no word with any hint."""
    text = "just show me who's on the hook for stuff"
    assert not score_contracts(text)
    r = route(text, fmt="xlsx", model_artifact="action_register")
    assert r.lane == "contract"
    assert r.contract == "action_register"


def test_low_confidence_asks_instead_of_guessing() -> None:
    r = route("gimme the blockers and who owns em", fmt="xlsx",
              model_artifact="action_register", model_confidence="low")
    assert r.reason == "ambiguous_artifact"
    assert "action_register" in r.candidates


def test_a_strong_lexical_disagreement_asks_rather_than_picking() -> None:
    """The user literally typed one artifact's name and the classifier
    read another. Neither side gets to win silently."""
    r = route("build me a risk register", fmt="xlsx",
              model_artifact="action_register")
    assert r.reason == "ambiguous_artifact"
    assert set(r.candidates) >= {"action_register", "risk_register"}


def test_the_classifier_cannot_override_the_computation_gate() -> None:
    """A model asked to judge whether its own arithmetic is trustworthy
    is not a control. The deterministic gates run first and win."""
    r = route("pivot this by region", fmt="xlsx", has_attachment=True,
              model_artifact="budget")
    assert r.lane == "provider"
    assert r.reason == "computation_over_attachment"

    r2 = route("make me a risk register", fmt="docx",
               model_artifact="risk_register")
    assert r2.lane == "provider"
    assert r2.reason == "format_not_renderable"


def test_an_unknown_artifact_label_is_ignored_not_trusted() -> None:
    """Fail-open: a classifier that returns garbage degrades to the
    lexical path rather than routing to a contract that does not exist."""
    r = route("make me a risk register", fmt="xlsx",
              model_artifact="not_a_real_contract")
    assert r.lane == "contract"
    assert r.contract == "risk_register"
