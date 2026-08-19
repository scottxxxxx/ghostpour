"""LiveRoundScore needs room for a whole round (2026-08-07).

TR shipped this mode and warned in the same message that it runs on
40-plus minute transcripts and that the token budget was worth a look.
It truncated on the second real call: 14,280 tokens in, output landing on
exactly 4,096, which is the file-level default and a ceiling rather than
a coincidence.

The mode returns ONE JSON object carrying questions[], each with a star
breakdown and five prose fields. So a cut-off response is not a shorter
score, it is unparseable, and on device it surfaced as a generic error.

That is the FAILURE WE WANT. The one to fear is a truncation the parser
tolerates, leaving a round scored on a prefix of itself: that reads as a
lenient scorer rather than a broken one, and it would be trended for
months before anyone questioned it.
"""

import json
import pathlib

CONFIG = pathlib.Path("config/remote/techrehearsal/response-analysis.json")
DEFAULT_THAT_TRUNCATED = 4096


def _doc():
    return json.loads(CONFIG.read_text())


def test_the_mode_sets_its_own_budget():
    """Without an override the mode inherits the file-level default, which
    is the exact value that truncated."""
    assert "LiveRoundScore" in _doc()["modes"]
    assert _doc()["modes"]["LiveRoundScore"].get("maxTokens") is not None


def test_the_budget_clears_the_value_that_truncated():
    budget = _doc()["modes"]["LiveRoundScore"]["maxTokens"]
    assert budget > DEFAULT_THAT_TRUNCATED


def test_there_is_real_headroom_not_a_tight_fit():
    """A ceiling chosen to just fit today's longest round re-truncates on a
    longer one. Output is billed on what is produced, not on what was
    allowed, so headroom costs nothing and a tight fit costs a scorecard."""
    assert _doc()["modes"]["LiveRoundScore"]["maxTokens"] >= 4 * DEFAULT_THAT_TRUNCATED


def test_the_file_default_is_never_lifted_to_buy_one_mode_room():
    """Only the modes that score a whole session need the room. Raising the
    file-level default would hand it to every response-analysis call,
    including short ones where a runaway response is the thing to cap.

    Amended 2026-08-19: this used to assert that NO mode but LiveRoundScore
    carried a ceiling, which was true when written and is no longer the
    property worth guarding. InterviewScorecard and ConversationPracticeScore
    have since been measured to scale with question count too and got
    ceilings of their own. What must stay true is the shape of the fix: a
    mode that needs room gets its OWN ceiling, and the default underneath
    them does not move, so a mode nobody has measured cannot inherit room
    it was never sized for.
    """
    doc = _doc()
    assert doc["maxTokens"] == DEFAULT_THAT_TRUNCATED, (
        "the file default moved, so every unmeasured mode just silently "
        "gained headroom")
    # InterviewFollowUp is the one that proves the default still guards:
    # it answers in ~140 tokens and has never been given an override.
    assert doc["modes"]["InterviewFollowUp"].get("maxTokens") is None

    # Every ceiling that DOES exist is one somebody sized on measurements.
    # A mode appearing here without a matching entry in
    # tests/test_tr_token_ceilings.py is a number nobody justified.
    sized = {"LiveRoundScore", "InterviewScorecard", "ConversationPracticeScore"}
    for name, mode in doc["modes"].items():
        if mode.get("maxTokens") is not None:
            assert name in sized, (
                f"{name} carries a ceiling that no measurement backs")
