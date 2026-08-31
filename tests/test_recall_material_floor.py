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

# The REAL block shape, copied from the incident request rather than
# simplified. The chrome matters: `(OVERDUE)` is parenthesised and
# `Projects:` / `People:` are leading labels, which is exactly what the
# structural stripping keys on. An idealised block would have tested the
# detector against a format that does not exist.
BLOCK = ("Projects: CTS (Ticket Creation System project)\n"
         "People: Don (FSU project manager); Sai\n"
         "[todo] Complete CTS Asset Management "
         "[owner: Vijay, by 2026-08-18 (OVERDUE)]")


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
    "next week [owner: unassigned, by 2026-08-18 (OVERDUE)]\n"
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


# --- locale independence: the reason the word list had to go ---

def test_chrome_stripping_survives_a_LOCALE_CHANGE():
    """The defect CQ caught, made into a test.

    The first version excluded chrome by an English word list. CQ's section
    labels are LOCALIZED per metadata.locale across five tables, while their
    flat markers stay English on purpose, so an English list fails
    ASYMMETRICALLY: perfect on English traffic, blind the moment a French or
    Japanese caller arrives, and nothing fails when they add a locale.

    Stripping by SHAPE (parenthesised markers, a leading `Word:` label) needs
    no shared vocabulary and no coordination. These blocks are the same
    content in three of their five locales.
    """
    cases = {
        "en": "Project: Acme\nPeople: Vijay (project manager)\n",
        "es": "Proyecto: Acme\nPersonas: Vijay (gerente de proyecto)\n",
        "fr": "Projet: Acme\nPersonnes: Vijay (chef de projet)\n",
    }
    for locale, block in cases.items():
        terms = _recital_terms(block, material="A podcast about China.")
        assert "Acme" in terms, f"{locale}: lost the real name"
        assert "Vijay" in terms, f"{locale}: lost the real name"
        for label in ("Project", "Proyecto", "Projet",
                      "People", "Personas", "Personnes"):
            assert label not in terms, (
                f"{locale}: section label {label!r} leaked into the name "
                f"predicate. An English word list would have caught only the "
                f"English one."
            )


def test_parenthesised_markers_are_stripped_whatever_they_say():
    """Flat markers stay English by design, but the rule must not depend on
    knowing which ones exist. Anything parenthesised is chrome."""
    block = ("[todo] renew the certificate (OVERDUE)\n"
             "[todo] file the report (due soon)\n"
             "(showing 2 of 9 open)\n"
             "(no stored memory about: Zenithcorp)")
    terms = _recital_terms(block, material="A podcast.")
    for marker in ("OVERDUE", "Zenithcorp"):
        assert marker not in terms, f"{marker!r} is inside chrome"


# --- CQ's REAL rendered block, not anyone's description of it ---

# Generated by CQ's shipped formatter and pasted verbatim. Every earlier
# fixture in this file was somebody's idea of the format, and each time the
# idealisation hid a defect: OVERDUE written without its parentheses, and
# then square-bracketed detail groups omitted entirely. CQ's own note on
# sending me the wrong vocabulary applies to fixtures too, and is the reason
# this one is copied rather than composed: a derived view cannot witness its
# own format.
REAL_BLOCK = """Projects: ABM
People: Srikant (lead engineer)

[todo] Ship the API gateway [owner: Reshmi, by 2026-08-28 (OVERDUE)]
[todo] Send the pricing model [owner: Srikant, by 2026-09-02 (due soon)]
[blocker] Timezone drift in the dashboard
[about you] Prefers written updates"""

MATERIAL = "A podcast about China and predictions."


def test_every_recited_line_is_counted_not_just_the_long_ones():
    """The miss CQ predicted and I then measured.

    With square-bracketed groups left in, the line's 5-grams were
    (ship, the, api, gateway, owner) while the answer's were
    (ship, the, api, gateway, and). Two recited lines were reported as one:
    a clean result on a genuine recital, inside the detector built to catch
    exactly that.
    """
    answer = ("I cannot summarize this recording. The context mentions "
              "Ship the API gateway and Timezone drift in the dashboard.")
    assert _recited_lines(REAL_BLOCK, MATERIAL, answer) == 2


def test_a_SHORT_line_is_covered_by_the_NAME_predicate_not_the_line_one():
    """Short lines are deliberately the name predicate's job.

    An earlier version of this test asserted the LINE predicate caught
    'Prefers written updates' (3 words). It did, and that was the wrong
    design: a short window is a loose match, and the not-in-the-material
    subtraction that normally protects against it is WEAKEST in exactly the
    trigger case, because a near-empty transcript has almost nothing to
    subtract. The noise would land precisely where the detector matters most.

    CQ measured the trade over 5,194 patch texts: 310 are ONE word ("Test",
    "Dana"), 563 are under four. But 555 of those 563 (99%) carry a
    capitalised token, so the name predicate already covers them, and only
    eight patches in the corpus are both short and caseless.

    So the coverage is not lost, it moved. Assert BOTH halves, or a future
    reader sees an abstention and "fixes" it by lowering the floor.
    """
    answer = "The context says: Prefers written updates."

    # The line predicate abstains, on purpose.
    assert _recited_lines(REAL_BLOCK, MATERIAL, answer) == 0

    # And the content is still covered, by the other predicate.
    assert "Prefers" in _recital_terms(REAL_BLOCK, MATERIAL)


def test_a_short_line_still_reaches_the_detector_end_to_end(caplog):
    """The half of the above that needs a fixture. Belt and braces: the
    abstention above must not become a silent hole at the detector level."""
    answer = "The context says: Prefers written updates."
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content=MATERIAL,
        metadata={"cq_recall_block": REAL_BLOCK, "call_type": "summary"},
    )
    resp = ChatResponse(text=answer, model="claude-haiku-4-5-20251001",
                        provider="anthropic")
    with caplog.at_level("WARNING"):
        _detect_recital(body, resp)
    rec = next(r for r in caplog.records if r.message == "cq_recital_detected")
    assert rec.__dict__["term_count"] >= 1
    assert rec.__dict__["line_count"] == 0


def test_one_word_patch_lines_cannot_fire_the_line_predicate():
    """310 patch texts in the corpus are a single word. 'Test' in a summary
    must not trip the detector on a turn with nothing to subtract."""
    block = "[fact] Test\n[person] Dana\n[org] Cbe"
    answer = ("This was a test recording and Dana was not mentioned in any "
              "meaningful way during the session.")
    assert _recited_lines(block, "A podcast.", answer) == 0


def test_bracket_chrome_does_not_cost_the_name_predicate_its_names():
    """Brackets are stripped for the LINE predicate only. `Reshmi` is a real
    person inside `[owner: ...]` and reciting them is the leak, so the name
    predicate must still see them."""
    terms = _recital_terms(REAL_BLOCK, MATERIAL)
    assert "Reshmi" in terms
    assert "Srikant" in terms
    assert "ABM" in terms
    for chrome in ("todo", "blocker", "owner", "OVERDUE", "Projects", "People"):
        assert chrome not in terms


def test_real_block_stays_silent_on_an_honest_refusal():
    assert _recited_lines(
        REAL_BLOCK, MATERIAL,
        "The material does not cover specific decisions or action items.") == 0


def test_lowercase_initial_brand_names_are_caught():
    """`eBay`, `iPhone`, `iOS`.

    The regex required an INITIAL capital, so a lowercase-initial brand
    evaded the name predicate completely: `\\b[A-Z]` cannot match at the `e`
    of eBay, and there is no word boundary before the `B`. Those are exactly
    the tokens a customer engagement is full of, and the gap would otherwise
    have been found by someone reciting an iPhone deployment.

    CQ surfaced it sideways: `eBay` turned up in a slice of caseless patches
    because their capitalisation proxy had the same shape as my regex and
    mis-bucketed it for the same reason.
    """
    block = ("[decided] migrate the eBay listing sync to iOS 26\n"
             "[todo] ship the iPhone build")
    terms = _recital_terms(block, material="A podcast about China.")
    for brand in ("eBay", "iOS", "iPhone"):
        assert brand in terms, f"{brand} evaded the name predicate"


def test_widened_token_does_not_swallow_ordinary_words():
    """The second arm requires an INTERNAL capital, so ordinary lowercase
    prose must not start matching. A noisy name predicate is one that gets
    switched off."""
    block = "[note] the team agreed to review the pricing model next week"
    assert _recital_terms(block, material="unrelated") == set()


# --- the detector must report BOTH ways, or its silence means nothing ---

def test_a_clean_turn_is_LOGGED_as_checked(caplog):
    """CQ's catch, and it is the same defect one layer up.

    Logging only on a hit leaves "ran and found nothing", "never ran" and
    "ran and crashed" as ONE observable, so the detector's own reachability
    could only ever be argued from the source. A silent detector answers "no
    recitals found" forever and looks exactly like a working one.

    Absence of BOTH events on a turn that received a block now means the
    detector did not run. That is the difference between a measurement and
    an inference, and it makes the denominator a query rather than a
    reconstruction.
    """
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content=MATERIAL,
        metadata={"cq_recall_block": REAL_BLOCK, "call_type": "summary"},
    )
    resp = ChatResponse(
        text="The material does not cover specific decisions or action items.",
        model="claude-haiku-4-5-20251001", provider="anthropic")

    with caplog.at_level("INFO"):
        _detect_recital(body, resp)

    checked = [r for r in caplog.records if r.message == "cq_recital_checked"]
    assert len(checked) == 1, "a clean turn must still say it was checked"
    d = checked[0].__dict__
    assert d["term_count"] == 0 and d["line_count"] == 0
    # The denominator fields have to be on the CLEAN event too, or the join
    # can only characterise the turns that fired.
    assert d["block_chars"] > 0
    assert d["response_chars"] > 0
    assert d["call_type"] == "summary"
    assert not any(r.message == "cq_recital_detected" for r in caplog.records)


def test_exactly_one_event_per_checked_turn(caplog):
    """Never both. A turn counted twice is worse than a turn not counted,
    because it inflates the denominator silently."""
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content=MATERIAL,
        metadata={"cq_recall_block": REAL_BLOCK, "call_type": "summary"},
    )
    for text in ("The material does not cover specific decisions.",
                 "The context mentions Timezone drift in the dashboard."):
        caplog.clear()
        with caplog.at_level("INFO"):
            _detect_recital(body, ChatResponse(
                text=text, model="m", provider="anthropic"))
        events = [r for r in caplog.records
                  if r.message in ("cq_recital_checked", "cq_recital_detected")]
        assert len(events) == 1, f"{len(events)} events for {text!r}"


def test_no_block_means_no_event(caplog):
    """A turn that never received a block was not CHECKED and must not be
    counted as one. The material gate already logs its own declines, so that
    population is visible from the other side."""
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="s", user_content=MATERIAL, metadata={"call_type": "summary"})
    with caplog.at_level("INFO"):
        _detect_recital(body, ChatResponse(
            text="A normal summary.", model="m", provider="anthropic"))
    assert not any(r.message.startswith("cq_recital_") for r in caplog.records)
