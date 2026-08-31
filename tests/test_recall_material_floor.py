"""Don't put a context block in a room that has nothing else in it.

#825 and #828 both act on the model AFTER it is already in the bad state.
Neither asked what puts it there. The incident turn answers that:

    material (31 seconds of podcast) :  608 chars
    injected context block            : 2261 chars
    context / material                :  3.7x

The model was asked to summarize almost nothing and handed a block nearly
four times the size holding a customer's people, overdue commitments and
decisions. It read that back, which is what a model does when the context IS
the only substantial content in the room.

This declines to create the state. It is a SECOND, independent layer rather
than a replacement: the deployed guard measures 0/20 on the incident turn,
and 0/20 leaves a 95% upper bound near 14% conditional on the trigger, which
is a residual worth a second mitigation rather than a rounding error.

The detector below is the other half. Both #825 and #828 were verified by
replaying one turn twenty times, which cannot see a small residual and cannot
see a recital phrased in a way nobody anticipated. That is not hypothetical:
#825's prohibition was defeated by the model inventing a politer way to say
the same thing.
"""

from app.models.chat import ChatRequest, ChatResponse
from app.services.features.context_quilt_hook import (
    _detect_recital,
    _material_below_floor,
    _recital_terms,
    _recited_lines,
)


def _req(content: str, call_type: str | None) -> ChatRequest:
    return ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="You are a meeting summarizer.",
        user_content=content,
        metadata={"call_type": call_type} if call_type else {},
    )


# --- the floor ---

def test_the_incident_turn_would_be_gated():
    """608 chars of transcript on the summary lane. The real case."""
    assert _material_below_floor(_req("x" * 608, "summary")) is True


def test_a_normal_summary_is_not_gated():
    """The smallest material that produced a genuine summary in sampled
    traffic was 963 chars. The floor must sit below it."""
    assert _material_below_floor(_req("x" * 963, "summary")) is False
    assert _material_below_floor(_req("x" * 9006, "summary")) is False


def test_chat_is_NEVER_gated_however_short():
    """The correction that mattered most in designing this.

    In chat the content is a QUESTION and the recall block is the intended
    answer source. "What did we decide about the promotion?" is 40
    characters. A floor calibrated on transcripts, applied here, would
    switch Meeting Memory off in chat entirely.

    CQ's measured population could not have caught this: all 362 of their
    calls are source_prompt=meeting_summary, so chat never reaches the path
    they measured. Their table sees one lane, this hook sees both.
    """
    for call_type in ("meeting_chat", "meeting_chat_follow_up",
                      "project_chat"):
        assert _material_below_floor(
            _req("What did we decide about the promotion?", call_type)
        ) is False, f"{call_type} must never be gated on material size"


def test_an_unknown_lane_fails_OPEN():
    """A new lane silently losing recall is a worse first failure than a new
    lane keeping it, since #828 already covers the model's behaviour."""
    assert _material_below_floor(_req("tiny", "some_future_lane")) is False
    assert _material_below_floor(_req("tiny", None)) is False


def test_floor_of_zero_disables_the_gate(monkeypatch):
    """0 must mean off, so it can be killed from config without a deploy."""
    import app.services.features.context_quilt_hook as hook
    monkeypatch.setattr(hook, "_material_floor_chars", lambda: 0)
    assert _material_below_floor(_req("x" * 10, "summary")) is False


# --- the detector ---

BLOCK = ("Projects: CTS (Ticket Creation System project)\n"
         "People: Don (FSU project manager); Sai\n"
         "[todo] Complete CTS Asset Management [owner: Vijay, OVERDUE]")


def test_detector_flags_a_name_that_came_only_from_the_context():
    terms = _recital_terms(BLOCK, material="A podcast about China.")
    assert "CTS" in terms
    assert any("Vijay" in t for t in terms)


def test_detector_does_NOT_flag_a_name_grounded_in_the_material():
    """The load-bearing half. In a meeting genuinely about that engagement
    the same names are in the transcript and belong in the summary. A
    detector that fires on those is a detector nobody will leave on."""
    terms = _recital_terms(
        BLOCK, material="We reviewed CTS Asset Management with Vijay today.")
    assert "CTS" not in terms
    assert not any("Vijay" in t for t in terms)


def test_detector_fires_on_the_real_incident_shape(caplog):
    """The exact sentence a guarded run produced, which is what proved #825
    incomplete. If the detector cannot see this, it is decorative."""
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content="A podcast about China.",
        metadata={"cq_recall_block": BLOCK, "call_type": "summary"},
    )
    resp = ChatResponse(
        text=("I cannot provide a summary. The discussion does not relate "
              "to any of the projects mentioned in the context (CTS, "
              "ABM/A2A Integration Platform)."),
        model="claude-haiku-4-5-20251001", provider="anthropic",
    )
    with caplog.at_level("WARNING"):
        _detect_recital(body, resp)
    assert any(r.message == "cq_recital_detected" for r in caplog.records)


def test_detector_silent_on_an_honest_refusal(caplog):
    """What #828 actually produces in prod. Must NOT fire, or the signal is
    all noise and gets turned off."""
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content="A podcast about China.",
        metadata={"cq_recall_block": BLOCK, "call_type": "summary"},
    )
    resp = ChatResponse(
        text="The material does not cover specific decisions or action items.",
        model="claude-haiku-4-5-20251001", provider="anthropic",
    )
    with caplog.at_level("WARNING"):
        _detect_recital(body, resp)
    assert not any(r.message == "cq_recital_detected" for r in caplog.records)


def test_detector_never_logs_the_terms_themselves(caplog):
    """The terms ARE the customer data this exists to protect. Logging them
    would turn a leak detector into a second leak."""
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content="A podcast about China.",
        metadata={"cq_recall_block": BLOCK, "call_type": "summary"},
    )
    resp = ChatResponse(
        text="Not related to CTS or Vijay's Asset Management work.",
        model="claude-haiku-4-5-20251001", provider="anthropic",
    )
    with caplog.at_level("WARNING"):
        _detect_recital(body, resp)
    rec = next(r for r in caplog.records if r.message == "cq_recital_detected")
    blob = str(rec.__dict__)
    for secret in ("CTS", "Vijay", "Asset Management", "Don", "Sai"):
        assert secret not in blob, f"detector logged {secret!r}"
    assert rec.__dict__["term_count"] >= 1


def test_detector_cannot_break_the_turn():
    """A watcher that can crash the thing it watches is a liability."""
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content="x",
        metadata={"cq_recall_block": BLOCK},
    )
    _detect_recital(body, object())  # not a ChatResponse at all


# --- CQ's correction: names are a proxy for the harm, not the harm ---

NAMELESS_BLOCK = (
    "[todo] finalize the quarterly pricing review before the end of "
    "next week [owner: unassigned, OVERDUE]\n"
    "[decided] the migration will be deferred until after the audit closes"
)


def test_a_recited_commitment_with_NO_name_in_it_is_caught():
    """CQ's gap, and it is the shape of the original incident.

    The leak that started all of this was an overdue customer COMMITMENT. A
    turn that recites another project's commitment carries the same
    confidentiality failure whether or not a proper noun rides along, and a
    name-keyed detector reports it clean.
    """
    material = "A podcast about China and predictions."
    answer = ("I cannot summarize this. It does not mention finalize the "
              "quarterly pricing review before the end of next week.")

    # The name check alone is BLIND to it. This assertion is the reason the
    # line signal exists; if it ever starts passing, the line signal has
    # become redundant and this test should be revisited rather than deleted.
    assert not _recital_terms(NAMELESS_BLOCK, material), \
        "block has no proper nouns; the name predicate cannot see this leak"

    assert _recited_lines(NAMELESS_BLOCK, material, answer) >= 1


def test_line_signal_does_not_fire_on_material_the_model_was_given():
    """Same not-in-the-material subtraction as the name check. If the
    transcript itself discussed the pricing review, echoing it is the job."""
    material = ("We spent the hour on how to finalize the quarterly pricing "
                "review before the end of next week, and agreed to defer.")
    answer = ("The team discussed how to finalize the quarterly pricing "
              "review before the end of next week.")
    assert _recited_lines(NAMELESS_BLOCK, material, answer) == 0


def test_line_signal_silent_on_an_honest_refusal():
    """What #828 produces. Ordinary phrasing must not collide with a block
    line, or the detector is noise and gets switched off."""
    assert _recited_lines(
        NAMELESS_BLOCK,
        "A podcast about China.",
        "The material does not cover specific decisions or action items.",
    ) == 0


def test_detector_fires_end_to_end_on_a_nameless_recital(caplog):
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content="A podcast about China.",
        metadata={"cq_recall_block": NAMELESS_BLOCK, "call_type": "summary"},
    )
    resp = ChatResponse(
        text=("Not related. The context covers the migration will be "
              "deferred until after the audit closes."),
        model="claude-haiku-4-5-20251001", provider="anthropic",
    )
    with caplog.at_level("WARNING"):
        _detect_recital(body, resp)
    rec = next(r for r in caplog.records if r.message == "cq_recital_detected")
    assert rec.__dict__["line_count"] >= 1
    assert rec.__dict__["term_count"] == 0, \
        "this leak is invisible to the name predicate; that is the point"
    # The no-logging-of-text rule survives the predicate change unchanged.
    blob = str(rec.__dict__)
    for secret in ("pricing review", "migration", "audit"):
        assert secret not in blob
