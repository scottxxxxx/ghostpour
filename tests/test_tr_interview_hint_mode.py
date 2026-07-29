"""GP-owned prompt for tr_mock_interview / prompt_mode InterviewHint.

The hint path was the last TR call still shipping a client-side prompt, so
our prompt edits never reached it. TR could not simply flip their managed
flag: with no InterviewHint mode, assembly falls through to the top-level
prompt, which is the question generator returning a `questions` array.

That fails badly rather than cleanly. TR confirmed there is no hint
decoder at all: the response is trimmed, checked non-empty, and rendered
straight into a SwiftUI Text view. A JSON blob is non-empty, so it would
have been displayed verbatim in the hint card.

Contract, per TR: plain UTF-8 prose, no JSON envelope, no markdown. The
only hard requirement is non-empty; an empty response becomes a typed
failure showing a canned fallback tip.
"""

import json

from app.services.prompt_assembly import assemble_prompt

SLUG = "techrehearsal/mock-interview"

# The three-part blob TR concatenates and sends as user_content.
USER_CONTENT = """ROLE: Staff Engineer at Acme

QUESTION THEY'RE STUCK ON: Tell me about a time you led a migration.

What this role values:  distributed systems; mentoring; incident response

CANDIDATE RESUME:
(no resume provided)"""


def _cfg():
    return json.load(open(f"config/remote/{SLUG}.json"))


def _asm(prompt_mode=None):
    return assemble_prompt(
        "tr_mock_interview", USER_CONTENT, {SLUG: _cfg()}, prompt_mode=prompt_mode
    )


def test_hint_mode_serves_its_own_prompt():
    hint = _asm("InterviewHint")
    sp = hint["system_prompt"]
    assert sp.startswith("You are a warm, supportive interview coach")
    for phrase in (
        "2 short paragraphs of plain text",
        "no markdown, no headings, no preamble",
        "STAR beats to hit",
        "never invent experience they don't have",
        "If the resume is thin",
    ):
        assert phrase in sp, f"missing hint guard: {phrase!r}"


def test_hint_contract_is_prose_never_json():
    # TR renders the raw string into a Text view with no parsing, so a JSON
    # response would be displayed verbatim to a candidate mid-interview.
    sp = _asm("InterviewHint")["system_prompt"]
    assert "Return plain UTF-8 text only" in sp
    assert "No JSON" in sp
    assert "never an empty response" in sp
    # The question-generator contract must not leak into this mode.
    assert "questions" not in sp


def test_hint_mode_budget_and_thinking():
    hint = _asm("InterviewHint")
    # Small budget for a two-paragraph nudge, so thinking has to be off:
    # on a think-by-default model max_tokens is shared with the reply.
    assert hint["max_tokens"] == 700
    assert hint["thinking"] == "disabled"


def test_user_content_passes_through_untouched():
    # userPromptTemplate is empty, so TR's three-part blob is the user turn
    # verbatim. Their format is fixed; we must not reshape it.
    assert _asm("InterviewHint")["user_content"] == USER_CONTENT


def test_other_modes_are_unaffected():
    gen = _asm("InterviewQuestionGen")
    assert gen["system_prompt"].startswith("You are an expert technical interviewer")
    assert gen["max_tokens"] == 4096
    assert "thinking" not in gen

    practice = _asm("ConversationPracticeGen")
    assert practice["system_prompt"].startswith("You are helping someone rehearse")

    # An unknown or absent mode still gets the top-level prompt unchanged.
    assert _asm(None)["system_prompt"] == gen["system_prompt"]
    assert _asm("NoSuchMode")["system_prompt"] == gen["system_prompt"]


def test_no_dashes_in_the_served_prompt():
    # Standing rule for every served prompt: the model copies the
    # punctuation it sees, and both apps ban em/en dashes in output.
    sp = _asm("InterviewHint")["system_prompt"]
    assert not [c for c in sp if c in "—–"]
    assert "Never use em dashes or en dashes" in sp
