"""A recall that happened must not look like a recall that did not.

2026-09-06: CQ saw two recall calls in their log with no `recall_echo`
line and no chat usage row on GP's side, and asked whether a recall leg
had been composed on a turn that did not ask for one. It had not; those
turns returned the generation OFFER envelope before any model call, so
`after_llm` never ran and the echo never reached the client. A field
present on ordinary turns and silently absent on one class of turn is the
instrument reading "no recall happened" when one did.
"""

from app.routers.chat import _with_recall_echo


ECHO = {"token_budget": 1200, "draft_intent": False}


def test_the_echo_rides_an_early_envelope():
    out = _with_recall_echo({"feature_state": {"cta": "x"}}, {"context_quilt": {"recall_echo": ECHO}})
    assert out["recall"] == ECHO and out["feature_state"] == {"cta": "x"}


def test_a_turn_that_composed_no_recall_carries_no_field():
    """Absent, not null: the memory-off control turn must stay clean, which
    is what SS read off the phone to prove the control was a control."""
    assert _with_recall_echo({"a": 1}, {}) == {"a": 1}
    assert _with_recall_echo({"a": 1}, {"context_quilt": {}}) == {"a": 1}


def test_an_envelope_that_already_carries_one_is_not_overwritten():
    out = _with_recall_echo({"recall": {"token_budget": 300}}, {"context_quilt": {"recall_echo": ECHO}})
    assert out["recall"] == {"token_budget": 300}


def test_the_three_offer_envelopes_use_it():
    src = open("app/routers/chat.py").read()
    assert src.count("_with_recall_echo(") == 4, "the helper plus three call sites"
    i = src.index("def _with_recall_echo")
    assert "after_llm` never runs" in src[i:i + 900]
