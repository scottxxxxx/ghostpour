"""Normalise a provider's finish reason into one word clients can branch on.

TR asked for this after a LiveRoundScore response truncated at the token
ceiling on 2026-08-07 and reached the device as unparseable JSON. Their fix
was a structural heuristic: scan for an unbalanced brace, ignoring braces
inside strings. It works, and it only works for shapes that fail visibly.
A mode returning a JSON array, or prose, or anything a lenient parser
tolerates, truncates silently and scores a round on a prefix of itself.

The information was always on the wire. Every provider adapter already puts
its own finish reason in `usage.finish_reason`. What was missing is that
the three vocabularies disagree, so a client branching on it would have to
know that "max_tokens", "MAX_TOKENS" and "length" are the same event, and
would silently stop working the day we route a model through a fourth.

So GP normalises and the client branches on one word.

The output vocabulary is deliberately OPEN, same as block_reason and the
gate-event fields. An unrecognised provider value passes through lowercased
rather than being flattened to "unknown", because a value we have not seen
is still evidence, and widening a vocabulary is safe for every shipped
build while retyping a field is not.
"""

from __future__ import annotations

# Provider value -> our word. Compared case-insensitively.
#
#   anthropic: end_turn, max_tokens, stop_sequence, tool_use, pause_turn
#   gemini:    STOP, MAX_TOKENS, SAFETY, RECITATION
#   openai:    stop, length, content_filter, tool_calls
_NORMALISED = {
    # The model said what it had to say.
    "end_turn": "complete",
    "stop_sequence": "complete",
    "stop": "complete",
    # The ceiling cut it off. THE one clients must be able to detect:
    # everything downstream of it is a fragment wearing the shape of an answer.
    "max_tokens": "max_tokens",
    "length": "max_tokens",
    # Content policy stopped it. Also incomplete, but not fixable by raising
    # a limit, so it must not collapse into max_tokens.
    "safety": "filtered",
    "recitation": "filtered",
    "content_filter": "filtered",
    # Mid-turn pauses. The turn is not over and nothing is missing.
    "tool_use": "tool_use",
    "tool_calls": "tool_use",
    "pause_turn": "tool_use",
}

TRUNCATED = frozenset({"max_tokens", "filtered"})


def normalise_stop_reason(usage: dict | None) -> str | None:
    """`usage.finish_reason` as one word, or None when the provider gave none.

    None means "we do not know", NOT "it completed". A client must not read
    a missing value as success: several providers omit the field entirely on
    the streaming path, and treating absence as completion is how a
    truncation gets rendered as a score.
    """
    if not isinstance(usage, dict):
        return None
    raw = usage.get("finish_reason")
    if not isinstance(raw, str) or not raw.strip():
        return None
    key = raw.strip().lower()
    return _NORMALISED.get(key, key)


def is_truncated(stop_reason: str | None) -> bool:
    """Did the model stop before finishing? Unknown is not truncated, and it
    is not complete either; callers that need certainty check for
    `stop_reason == "complete"` instead."""
    return stop_reason in TRUNCATED
