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


def test_the_other_modes_are_untouched():
    """Only the mode that scores a whole live round needs the room. Raising
    the file-level default would hand it to every response-analysis call,
    including short ones where a runaway response is the thing to cap."""
    doc = _doc()
    assert doc["maxTokens"] == DEFAULT_THAT_TRUNCATED
    for name, mode in doc["modes"].items():
        if name != "LiveRoundScore":
            assert mode.get("maxTokens") is None, name
