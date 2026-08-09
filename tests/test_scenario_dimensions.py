"""Dimensions branch by conversation kind (2026-08-09, Scott).

Exactly the bug July already fixed for RATINGS, pointing the other way.
The 2026-07-16 grader eval branched rating_anchors by kind because a STAR
rubric was grading hard conversations. The five DIMENSIONS were left
hard-coded, so Clarity / Empathy / Confidence / Boundaries / Judgment,
written for a difficult personal conversation, were grading job
interviews. Empathy is the wrong virtue there, and Boundaries is the right
one wearing a name borrowed from somewhere else.

Comparability is preserved where it means something. WITHIN a scenario the
practice and live scorers interpolate the same block, so a rehearsal and
the real round stay directly comparable, which is the entire reason
LiveRoundScore reuses that rubric. ACROSS kinds they differ, which is
correct: an interview score and a family-conversation score were never
comparable to begin with.
"""

import json
import pathlib

from app.services.prompt_assembly import _apply_scenario

CONFIG = json.loads(
    pathlib.Path("config/remote/techrehearsal/response-analysis.json").read_text())

INTERVIEW_KINDS = ("jobInterview", "interview")
PERSONAL_KINDS = ("hardConversation", "personal", "repairConversation",
                  "protectConversation")
NEGOTIATION_KINDS = ("payNegotiation", "purchaseNegotiation", "negotiation")


def _resolved(kind, mode="LiveRoundScore"):
    return _apply_scenario(CONFIG["modes"][mode]["systemPrompt"], CONFIG, kind, None)


# --- the fix ----------------------------------------------------------


def test_an_interview_is_not_graded_on_empathy_and_boundaries():
    """The specific complaint. Both were written for a personal
    conversation and neither is what an interviewer scores."""
    sp = _resolved("jobInterview")
    assert '"Structure"' in sp and '"Specificity"' in sp and '"Ownership"' in sp
    assert '"Empathy"' not in sp


def test_a_hard_conversation_keeps_the_dimensions_written_for_it():
    """The five were right all along, for the conversation they were
    written for. This is a routing fix, not a repudiation."""
    sp = _resolved("hardConversation")
    for name in ("Clarity", "Empathy", "Confidence", "Boundaries", "Judgment"):
        assert f'"{name}"' in sp


def test_a_negotiation_gets_its_own_set():
    sp = _resolved("payNegotiation")
    assert '"Anchoring"' in sp and '"Preparation"' in sp


def test_the_interview_dimensions_measure_what_the_interview_anchors_grade():
    """Before this, the anchors rewarded a complete STAR arc with a
    quantified result and personal ownership, while the dimensions scored
    empathy. The two halves of one scorecard were measuring different
    things."""
    sp = _resolved("jobInterview")
    anchors = CONFIG["scenarios"]["jobInterview"]["rating_anchors"]
    assert "STAR" in anchors and "QUANTIFIED" in anchors
    assert "STAR arc" in sp          # Structure
    assert "numbers" in sp           # Specificity
    assert "I versus we" in sp       # Ownership


# --- what must not break ----------------------------------------------


def test_practice_and_live_share_a_block_within_a_kind():
    """The comparability TR asked us to protect, and the only kind that was
    ever real: your rehearsal against your own real round."""
    for kind in INTERVIEW_KINDS + PERSONAL_KINDS + NEGOTIATION_KINDS:
        live = _resolved(kind, "LiveRoundScore")
        practice = _resolved(kind, "ConversationPracticeScore")
        names = lambda s: {ln.split('"')[3] for ln in s.splitlines()
                           if ln.strip().startswith('{ "name"')}
        assert names(live) == names(practice), kind


def test_every_kind_resolves_to_five_named_dimensions():
    """The client drops the card below three, so a kind that resolved to
    fewer would silently lose the section rather than error."""
    for kind in INTERVIEW_KINDS + PERSONAL_KINDS + NEGOTIATION_KINDS:
        sp = _resolved(kind)
        assert sp.count('{ "name"') == 5, kind
        assert "{{dimensions}}" not in sp


def test_an_unknown_kind_still_gets_dimensions():
    """Falls back to scenarioDefaults, which is today's behaviour. A
    dimensionless scorer would drop the whole card."""
    for kind in (None, "somethingNew"):
        sp = _resolved(kind)
        assert "{{dimensions}}" not in sp
        assert sp.count('{ "name"') == 5


def test_live_scoring_finally_sees_the_anchors():
    """The live scorer graded against no anchors at all while the practice
    scorer graded against STAR, which is why a real round and a rehearsal
    were never really on one rubric."""
    assert "{{rating_anchors}}" in CONFIG["modes"]["LiveRoundScore"]["systemPrompt"]
    assert "RATING ANCHORS" in _resolved("jobInterview")


def test_the_rehearsal_carry_is_background_not_a_baseline():
    """The headline read "but again failed", which is a comparison to the
    rehearsal leaking into a score of the real performance. Plan versus
    reality is tr_compare_reality's job, not this one's."""
    sp = " ".join(_resolved("jobInterview").split())
    assert "It is not a baseline and this is not a comparison" in sp
    assert "never write \"again\" or \"still\"" in sp
