"""`stop_reason` on the wire (2026-08-07, TR ask).

A LiveRoundScore response truncated at the token ceiling and reached the
device as unparseable JSON. TR's fix was a structural heuristic: scan for
an unbalanced brace, ignoring braces inside strings. It works, and it only
works for shapes that fail visibly. A mode returning a JSON array, or
prose, or anything a lenient parser tolerates, truncates silently and
scores a round on a prefix of itself.

The information was always on the wire in `usage.finish_reason`. What was
missing is that the provider vocabularies disagree, so a client branching
on it had to know "max_tokens", "MAX_TOKENS" and "length" are one event,
and would silently stop working the day a model routes through a fourth
provider.
"""

import pytest

from app.services.stop_reason import is_truncated, normalise_stop_reason


# --- the three vocabularies are one vocabulary -----------------------


@pytest.mark.parametrize("raw", ["max_tokens", "MAX_TOKENS", "length", "Length"])
def test_every_provider_word_for_the_ceiling_normalises_to_one(raw):
    """The whole point. Anthropic, Gemini and OpenAI-compatible providers
    each have their own word for the same event."""
    assert normalise_stop_reason({"finish_reason": raw}) == "max_tokens"


@pytest.mark.parametrize("raw", ["end_turn", "stop_sequence", "STOP", "stop"])
def test_every_provider_word_for_finishing_normalises_to_one(raw):
    assert normalise_stop_reason({"finish_reason": raw}) == "complete"


@pytest.mark.parametrize("raw", ["SAFETY", "RECITATION", "content_filter"])
def test_a_policy_stop_does_not_collapse_into_the_ceiling(raw):
    """Both are incomplete, but only one is fixable by raising a limit.
    Merging them would send someone to change maxTokens for a response the
    model refused to finish."""
    assert normalise_stop_reason({"finish_reason": raw}) == "filtered"
    assert is_truncated("filtered")


# --- absence is not success ------------------------------------------


@pytest.mark.parametrize("usage", [None, {}, {"finish_reason": None},
                                   {"finish_reason": ""}, {"finish_reason": "  "},
                                   "not-a-dict", {"finish_reason": 7}])
def test_no_reason_means_unknown_and_the_field_is_absent(usage):
    """None means "we do not know", never "it completed". Several providers
    omit the field on the streaming path, and reading absence as success is
    exactly how a truncation gets rendered as a score."""
    assert normalise_stop_reason(usage) is None


def test_unknown_is_not_truncated_and_is_not_complete_either():
    """A caller that needs certainty checks for "complete" rather than
    checking that it is not truncated."""
    assert not is_truncated(None)
    assert normalise_stop_reason(None) != "complete"


# --- the vocabulary stays open ---------------------------------------


def test_an_unrecognised_provider_value_passes_through():
    """Same posture as block_reason and the gate-event fields. A value we
    have not seen is still evidence, and flattening it to "unknown" throws
    away the one clue anyone would have when a new provider misbehaves."""
    assert normalise_stop_reason({"finish_reason": "some_new_reason"}) == "some_new_reason"


def test_an_unrecognised_value_is_not_assumed_to_be_a_failure():
    """Guessing wrong in that direction turns every future provider quirk
    into a spurious "cut short" message on a complete answer."""
    assert not is_truncated("some_new_reason")


def test_tool_pauses_are_not_truncation():
    """A mid-turn pause means the turn is not over, not that anything is
    missing. Calling it truncated would fire the client's cut-short
    message on a perfectly healthy multi-step turn."""
    for raw in ("tool_use", "tool_calls", "pause_turn"):
        assert normalise_stop_reason({"finish_reason": raw}) == "tool_use"
    assert not is_truncated("tool_use")


# --- both transports carry it ----------------------------------------


def test_the_streaming_done_event_carries_it():
    src = open("app/routers/chat.py").read()
    block = src[src.index('"type": "done"'):]
    block = block[:block.index("if search_state is not None")] + block[:2000]
    assert "stop_reason" in block


def test_the_json_response_model_carries_it():
    from app.models.chat import ChatResponse
    assert "stop_reason" in ChatResponse.model_fields
    assert ChatResponse(text="x", model="m", provider="p").stop_reason is None
