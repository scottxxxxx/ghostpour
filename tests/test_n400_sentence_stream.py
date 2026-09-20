"""Sentence release for the interviewer lane: never a partial sentence, never
a sentence a guard would have stopped, and the last one held.

Scott, 2026-09-19: the app does TTS on the reply, so it can start speaking
before the turn finishes, but it must never start a sentence and stop mid
sentence. The rule under test is the one in app/services/n400_sentence_stream.py,
built from what each guard actually reads. Every test here feeds the model's
text as DELTAS, because that is how it arrives and the one thing a whole-text
test cannot prove is that a boundary split across two deltas still lands.
"""

import json

import pytest

from app.services import n400_sentence_stream as ss
from app.services.n400_sentence_stream import (
    ReleaseController, ReplyStreamParser, SentenceSplitter,
)


# --- helpers ------------------------------------------------------------

def _chars(text: str):
    """Feed one character at a time: the worst-case delta split."""
    return [text[i] for i in range(len(text))]


def _sentences(text: str, chunks=None) -> list[str]:
    sp = SentenceSplitter()
    out = []
    for c in (chunks or [text]):
        out += [e[1] for e in sp.feed(c) if e[0] == "sentence"]
    out += [e[1] for e in sp.close() if e[0] == "sentence"]
    return out


def _obj(order, **over):
    base = {"schema_version": 1, "turn_id": "t1", "intent": "answer",
            "facts": [], "deferred": [], "clarification": None, "conflict": None,
            "escalation": None, "complete": False, "asking": None,
            "section_checkpoint": None, "interview_over": False,
            "reply": {"en": "Got it, six years on your own. Now, what is your A-Number?"}}
    base.update(over)
    return json.dumps({k: base[k] for k in order}, ensure_ascii=False)


GOOD = ["schema_version", "turn_id", "intent", "facts", "deferred", "clarification",
        "conflict", "escalation", "complete", "asking", "section_checkpoint",
        "interview_over", "reply"]
REPLY_FIRST = ["schema_version", "turn_id", "reply", "intent", "facts", "deferred",
               "clarification", "conflict", "escalation", "complete", "asking",
               "section_checkpoint", "interview_over"]

AGENDA_P3 = ("q_p3_eyes | Part 3: Biographic | p3.eye_color | What color are your eyes?\n"
             "q_p3_hair | Part 3: Biographic | p3.hair_color | What color is your hair?\n")


def _run(text: str, chunks=None, **kw) -> tuple[list[str], ss.ReleaseResult]:
    rc = ReleaseController(locale=kw.pop("locale", "en"), agenda=kw.pop("agenda", None),
                           known_facts=kw.pop("known_facts", None), turn_id="t1")
    released = []
    for c in (chunks or [text]):
        released += [e["text"] for e in rc.feed(c)]
    return released, rc.close()


# --- 1. the splitter ----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Got it. What is your A-Number?", ["Got it.", "What is your A-Number?"]),
    # Abbreviations and initials never end a sentence.
    ("Mr. Smith is here. Thank you.", ["Mr. Smith is here.", "Thank you."]),
    ("You are a U.S. citizen now. Congratulations.", ["You are a U.S. citizen now.", "Congratulations."]),
    ("Dr. J. Alvarez signed it. Good.", ["Dr. J. Alvarez signed it.", "Good."]),
    # A decimal is not a boundary.
    ("It took 1.5 years. Then it was done.", ["It took 1.5 years.", "Then it was done."]),
    # Closing quotes and brackets stay with the sentence they close.
    ('She said "yes." Then she left.', ['She said "yes."', "Then she left."]),
    # Spanish openers belong to the sentence they open.
    ("Seis años por su cuenta. ¿Cuál es su número A?", ["Seis años por su cuenta.", "¿Cuál es su número A?"]),
    ("¡Perfecto! ¿Y su dirección?", ["¡Perfecto!", "¿Y su dirección?"]),
    # Ellipsis and stacked terminators.
    ("Well… let me think. Ok?!", ["Well…", "let me think.", "Ok?!"]),
    # The tail without a terminator is flushed on close.
    ("First one. and a trailing fragment", ["First one.", "and a trailing fragment"]),
    ("Only one sentence", ["Only one sentence"]),
])
def test_splitter_boundaries(text, expected):
    assert _sentences(text) == expected


@pytest.mark.parametrize("text", [
    "Got it. What is your A-Number?",
    "Mr. Smith is here. You are a U.S. citizen now. It took 1.5 years.",
    'She said "yes." ¿Cuál es su número A? Well… ok.',
])
def test_splitter_is_delta_split_invariant(text):
    """The property that matters for a stream: however the deltas fall, the
    sentences are the same. One character at a time is the worst case."""
    assert _sentences(text, _chars(text)) == _sentences(text)


def test_splitter_signals_the_start_of_the_next_sentence():
    """`started` is what lets the controller release sentence k the moment
    sentence k+1 begins, rather than waiting for k+1 to complete."""
    sp = SentenceSplitter()
    ev = sp.feed("First. Sec")
    kinds = [e[0] for e in ev]
    assert kinds == ["started", "sentence", "started"], kinds


# --- 2. the parser ------------------------------------------------------

def _parse(text: str, chunks=None, locale="en"):
    p = ReplyStreamParser(locale)
    ev = []
    for c in (chunks or [text]):
        ev += p.feed(c)
    return p, ev


def test_parser_reports_keys_before_reply_and_streams_reply_text():
    text = _obj(GOOD, facts=[{"field_id": "p1.x", "value": "y"}],
                section_checkpoint={"part": 1})
    p, ev = _parse(text, _chars(text))
    done = {e[1]: e[2] for e in ev if e[0] == "key_done"}
    assert done["facts"] == [{"field_id": "p1.x", "value": "y"}]
    assert done["deferred"] == []
    assert done["section_checkpoint"] == {"part": 1}
    kinds = [e[0] for e in ev]
    assert kinds.index("reply_start") > kinds.index("key_done")
    streamed = "".join(e[1] for e in ev if e[0] == "reply_text")
    assert streamed == "Got it, six years on your own. Now, what is your A-Number?"
    assert "reply_end" in kinds
    assert p.malformed is None


def test_parser_streams_only_the_requested_locale():
    text = _obj(GOOD, reply={"es": "Seis años. ¿Su número A?", "en": "Six years. Your A-Number?"})
    _, ev = _parse(text, _chars(text), locale="es")
    assert "".join(e[1] for e in ev if e[0] == "reply_text") == "Seis años. ¿Su número A?"
    _, ev = _parse(text, _chars(text), locale="en")
    assert "".join(e[1] for e in ev if e[0] == "reply_text") == "Six years. Your A-Number?"


def test_parser_handles_a_bare_string_reply():
    text = _obj(GOOD, reply="Bare string reply. Question?")
    _, ev = _parse(text, _chars(text))
    assert "".join(e[1] for e in ev if e[0] == "reply_text") == "Bare string reply. Question?"


def test_parser_decodes_escapes_split_across_deltas():
    reply = 'She said \\"yes\\".\\nNi\\u00f1o \\ud83d\\ude00 ok.'
    text = '{"facts": [], "deferred": [], "section_checkpoint": null, "reply": {"en": "' + reply + '"}}'
    _, ev = _parse(text, _chars(text))
    assert "".join(e[1] for e in ev if e[0] == "reply_text") == 'She said "yes".\nNiño 😀 ok.'


def test_parser_ignores_prose_before_the_object():
    """The extract_envelope case: deliberation, then the object."""
    text = "Let me think about this carefully.\n\n" + _obj(GOOD)
    _, ev = _parse(text, _chars(text))
    assert "".join(e[1] for e in ev if e[0] == "reply_text").startswith("Got it")


def test_parser_reports_reply_before_the_keys():
    text = _obj(REPLY_FIRST)
    p, ev = _parse(text, _chars(text))
    kinds = [e[0] for e in ev]
    start = kinds.index("reply_start")
    before = {e[1] for e in ev[:start] if e[0] == "key_done"}
    # schema_version and turn_id legitimately precede reply here; what must
    # NOT is any of the three keys the checkpoint refusal reads.
    assert before == {"schema_version", "turn_id"}
    assert not (before & set(ss.KEYS_BEFORE_REPLY))


def test_parser_flags_malformed_and_stops():
    _, ev = _parse('{"facts": [], "reply": {"en": "hi"} , ]')
    assert any(e[0] == "malformed" for e in ev)


# --- 3. the release rule --------------------------------------------------

def test_releases_all_but_the_last_sentence_and_holds_it():
    """The whole point, plus auditor ruling 1: the question waits for the
    envelope, the read-back does not."""
    text = _obj(GOOD, reply={"en": "Six years on your own, noted. That is the general five year path. Now, what is your A-Number?"})
    released, res = _run(text, _chars(text))
    assert released == ["Six years on your own, noted.", "That is the general five year path."]
    assert res.held == "Now, what is your A-Number?"
    assert res.buffered_reason is None


def test_a_single_sentence_reply_releases_nothing_and_holds_it():
    text = _obj(GOOD, reply={"en": "What is your A-Number?"})
    released, res = _run(text, _chars(text))
    assert released == []
    assert res.held == "What is your A-Number?"


def test_sentence_k_is_released_when_k_plus_1_begins_not_when_it_ends():
    text = _obj(GOOD, reply={"en": "First sentence. Second sentence that is still being written"})
    rc = ReleaseController(locale="en", agenda=None, known_facts=None)
    head = text[:text.index("Second sentence that")] + "Sec"
    released = [e["text"] for e in rc.feed(head)]
    assert released == ["First sentence."], "released as soon as the next sentence began"


def test_reply_before_the_keys_buffers_the_turn():
    """Rule 1. The carrier is the parser refusing, not the prompt remembering."""
    released, res = _run(_obj(REPLY_FIRST), _chars(_obj(REPLY_FIRST)))
    assert released == []
    assert res.held is None
    assert res.buffered_reason == "reply_before_section_checkpoint,facts,deferred"


def test_checkpoint_the_full_check_would_refuse_buffers_the_turn():
    """Rule 2, and the pre-check must agree with the full check: with no
    facts settling p3.hair_color, a Part 3 checkpoint contradicts the agenda."""
    text = _obj(GOOD, section_checkpoint={"part": 3},
                reply={"en": "That completes Part 3. Now Part 4. What is your address?"})
    released, res = _run(text, _chars(text), agenda=AGENDA_P3)
    assert released == []
    assert res.buffered_reason == "checkpoint_refused"


def test_checkpoint_the_full_check_would_allow_streams():
    """The other half of rule 2, and the reason facts must precede reply:
    this response's own facts close the part, exactly the read-back shape
    conf-v25b produced eight false positives on."""
    text = _obj(GOOD, section_checkpoint={"part": 3},
                facts=[{"field_id": "p3.eye_color", "value": "brown"},
                       {"field_id": "p3.hair_color", "value": "black"}],
                reply={"en": "Brown eyes and black hair, got it. That completes Part 3. Is that right?"})
    released, res = _run(text, _chars(text), agenda=AGENDA_P3)
    assert released == ["Brown eyes and black hair, got it.", "That completes Part 3."]
    assert res.held == "Is that right?"
    assert res.buffered_reason is None


def test_an_oath_modification_offer_is_never_released():
    """Rule 3: the one harm no later correction reaches. The sentence before
    it was individually clean and stays released; the offer and everything
    after it ride the envelope, where the tail refuses and retries."""
    text = _obj(GOOD, reply={"en": (
        "You will take the Oath of Allegiance. "
        "You can request a modification to the work of national importance clause. "
        "Shall we continue?")})
    released, res = _run(text, _chars(text))
    assert released == ["You will take the Oath of Allegiance."]
    assert res.held is None
    assert res.buffered_reason == "oath_modification"


def test_a_denial_of_the_modification_is_fine():
    """The negation governs: a correct denial must not buffer."""
    text = _obj(GOOD, reply={"en": (
        "You may not request a modification to the work of national importance clause. "
        "Shall we continue?")})
    released, res = _run(text, _chars(text))
    assert released == ["You may not request a modification to the work of national importance clause."]
    assert res.buffered_reason is None


def test_an_oath_node_on_the_agenda_buffers_the_whole_turn():
    """Auditor ruling 3: Part 9 is where the wording is the product."""
    agenda = "q_p9_oath | Part 9: Oath | p9.willing_bear_arms | Are you willing to bear arms?\n"
    released, res = _run(_obj(GOOD), _chars(_obj(GOOD)), agenda=agenda)
    assert released == []
    assert res.buffered_reason == "oath_node_on_agenda"


def test_prose_releases_nothing():
    """Rule 6: no reply key at object depth, so nothing streams and the tail
    retries as today."""
    released, res = _run("I think the applicant means she has lived here six years.")
    assert released == []
    assert res.held is None


def test_malformed_object_stops_release():
    text = '{"facts": [], "deferred": [], "section_checkpoint": null, "reply": {"en": "One. Two. Three"} ]]]'
    released, res = _run(text, _chars(text))
    assert res.buffered_reason and res.buffered_reason.startswith("malformed")


def test_release_is_delta_split_invariant():
    """Whole text and one-character deltas must release the same sentences."""
    text = _obj(GOOD, reply={"en": 'Mr. Smith, noted. She said "yes." ¿Y su número A?'})
    whole, r1 = _run(text)
    chars, r2 = _run(text, _chars(text))
    assert whole == chars == ["Mr. Smith, noted.", 'She said "yes."']
    assert r1.held == r2.held == "¿Y su número A?"


def test_agenda_carries_oath():
    assert ss.agenda_carries_oath("q_p9_oath | Part 9: Oath | p9.x | q?")
    assert ss.agenda_carries_oath("q_p2_x | Part 2: Info | p9.oath_disability | q?")
    assert not ss.agenda_carries_oath(AGENDA_P3)
    assert not ss.agenda_carries_oath(None)
