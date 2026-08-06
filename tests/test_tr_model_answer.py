"""tr_mock_interview / InterviewModelAnswer (2026-08-06, TR).

A candidate looking at their generated question list taps one and gets an
example of what a strong answer sounds like. It is study material, so it
gives the whole answer. Not the Hint, which fires mid-answer when someone
has stalled and deliberately withholds the answer to keep them talking.

Two things in this prompt are load-bearing on TR's side rather than
stylistic, and neither is obvious from reading the wording:

1. `Why this works: ` is a PARSING BOUNDARY. The client splits the output
   on that literal string so the reasoning renders as separate commentary.
   Reword it and the rationale lands inside the answer the candidate
   practises saying out loud, in their own voice, as if it were part of
   the answer. It would read as fluent nonsense rather than as an error,
   which is why a test guards it instead of a comment.

2. The output is plain prose. This path has no decoder and renders
   verbatim into a Text view, so JSON or a code fence reaches the user as
   visible punctuation.

The fabrication constraint is TR's one explicit ask not to soften, and it
is the difference between study material and a candidate confidently
reciting an achievement they do not have, in an interview, out loud.
"""

import json
import pathlib

import pytest

CONFIG = pathlib.Path("config/remote/techrehearsal/mock-interview.json")
MARKER = "Why this works: "


@pytest.fixture(scope="module")
def mode():
    doc = json.loads(CONFIG.read_text())
    assert "InterviewModelAnswer" in doc["modes"], (
        "the mode is gone; TR calls tr_mock_interview with "
        "prompt_mode=InterviewModelAnswer and would fall back to the "
        "question-generation prompt, which returns JSON into a Text view")
    return doc["modes"]["InterviewModelAnswer"]


def test_the_parsing_boundary_is_intact(mode):
    """The exact string, with its trailing space, quoted as an instruction.
    TR splits on it. If this fails, do not just re-add the marker: tell TR
    in the same breath, because their split point and our wording have to
    move together."""
    assert f'beginning exactly "{MARKER}"' in mode["systemPrompt"]


def test_the_answer_is_plain_prose(mode):
    """No decoder on this path. A code fence renders as visible backticks."""
    sp = mode["systemPrompt"]
    assert "No JSON, no code fences" in sp
    assert "no markdown" in sp


def test_fabrication_is_forbidden_in_the_strongest_terms(mode):
    """TR's one explicit ask not to soften. A model answer is recited out
    loud in a real interview, so an invented metric is not a bad sentence,
    it is a candidate claiming credit for something that did not happen."""
    sp = mode["systemPrompt"]
    assert "NEVER invent an achievement, a metric, a company or a title" in sp
    assert "leave it out rather than fabricating it" in sp


def test_it_is_not_the_hint(mode):
    """The Hint withholds the answer on purpose to keep a stalled candidate
    talking. This one gives the whole thing. Collapsing the two would make
    the study material useless in one direction and the mid-answer nudge a
    script to read from in the other."""
    doc = json.loads(CONFIG.read_text())
    hint = doc["modes"]["InterviewHint"]["systemPrompt"]
    assert "stuck mid-interview" in hint
    assert "BEFORE they attempt it" in mode["systemPrompt"]
    assert mode["systemPrompt"] != hint


def test_no_dashes_are_served(mode):
    """House rule, and it applies to a prompt we did not write. The model
    copies the punctuation it sees."""
    sp = mode["systemPrompt"]
    assert "Never use em dashes or en dashes" in sp
    for dash in ("—", "–"):
        assert dash not in sp, "the prompt itself must not contain one"
