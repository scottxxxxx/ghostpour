"""LiveRoundScore is GP's prompt now (2026-08-08).

Absorbed from TR's handover. Scott's ruling, restated so nobody
re-litigates it: no prompt is the client's to own. The client provides the
text once, GP serves and controls it from then on, and a wording fix is a
middleware change rather than an App Store review cycle.

This mode is the urgent one because it is incident-adjacent twice over.
It truncated on a real 44-minute round because per-mode budgets silently
no-op for bootstrap-prompted calls, and it was extended client-side
without our review, which is the thing the doctrine exists to prevent.
"""

import json
import pathlib

CONFIG = pathlib.Path("config/remote/techrehearsal/response-analysis.json")


def _mode(name="LiveRoundScore"):
    return json.loads(CONFIG.read_text())["modes"][name]


def _flat(s):
    return " ".join(s.split())


def test_gp_holds_the_prompt_not_just_the_budget():
    """Holding maxTokens without systemPrompt is what made the ceiling fix
    inert: assembly never ran, so the mode config was never consulted."""
    m = _mode()
    assert m.get("systemPrompt", "").strip()
    assert m.get("maxTokens") == 16384


def test_the_client_decode_contract_survives_absorption():
    """TR's app decodes these keys by name. Absorption is a transfer of
    ownership, not a licence to rename fields under a shipped build.

    Checked AFTER scenario interpolation, because `dimensions` now arrives
    through {{dimensions}} rather than inline. Asserting against the raw
    template would have passed while the served prompt was missing the key
    entirely, which is the wrong direction for a contract test."""
    from app.services.prompt_assembly import _apply_scenario
    sp = _apply_scenario(_mode()["systemPrompt"],
                         json.loads(CONFIG.read_text()), "jobInterview", None)
    for key in ("overall", "headline", "biggest_gap_title", "biggest_gap_detail",
                "next_best_sentence", "dimensions", "questions"):
        assert f'"{key}"' in sp, key
    assert "{{" not in sp, "an unresolved placeholder would ship as literal text"


def test_both_scorers_take_their_dimensions_from_the_same_place():
    """Superseded in substance by tests/test_scenario_dimensions.py, kept
    as the structural half.

    The names are no longer fixed: they branch by conversation kind, since
    Clarity/Empathy/Confidence/Boundaries/Judgment were written for a
    difficult personal conversation and were grading job interviews. What
    still has to hold is that the live and practice scorers read from ONE
    source, so a rehearsal and the real round stay comparable within a
    kind."""
    doc = json.loads(CONFIG.read_text())
    for mode in ("LiveRoundScore", "ConversationPracticeScore"):
        sp = doc["modes"][mode]["systemPrompt"]
        assert "{{dimensions}}" in sp, mode
        assert '"Empathy"' not in sp, f"{mode} still hard-codes a dimension set"


def test_diarization_errors_do_not_cost_the_candidate():
    """TR asked for this one to survive absorption and they were right to.
    Speaker labels are wrong often enough that scoring by label would
    penalise a candidate for the interviewer's words, which is the one
    unfair failure a scorer can have."""
    sp = _flat(_mode()["systemPrompt"])
    assert "diarization errors" in sp
    assert "Attribute by content" in sp
    assert "never penalize the candidate for the interviewer's words" in sp


def test_it_scores_only_what_was_said():
    """Same fabrication posture as the model answers, and higher stakes: a
    score is a number the candidate will trend over months, so an invented
    question inflates or deflates a trend rather than reading as an odd
    sentence somebody notices."""
    sp = _flat(_mode()["systemPrompt"])
    assert "Score only what was actually said" in sp
    assert "Never invent a question the interviewer did not ask" in sp


def test_a_thin_transcript_says_so_rather_than_guessing():
    """The addition we made on absorption. Five dimensions always render,
    so without this the model fills all five whatever the evidence, and a
    confident number on no evidence is worse than an honest blank."""
    sp = _flat(_mode()["systemPrompt"])
    assert "too thin to judge a dimension, say so in its note" in sp


def test_no_dashes():
    sp = _mode()["systemPrompt"]
    assert "Never use em dashes or en dashes" in sp
    for dash in ("—", "–"):
        assert dash not in sp
