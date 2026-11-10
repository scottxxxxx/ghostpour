"""Scott's build 216 walk (case f58ab24a, 2026-11-10), three lane defects.

1. Parts 12, 1 and 2 were read back with the next part's question in the
   same reply, no "is that right?". Each carried section_checkpoint with
   awaiting_confirmation FALSE and `asking` on the next node. The client's
   SECTION BOUNDARY still said "without asking for confirmation" (the v39
   text, retired 2026-10-16), and the lane followed it. Part 3, which that
   text did not reach, was confirmed properly.
2. "January 1st, 2026." then, asked about the impossible year, "2016." The
   lane deferred a year only; she had corrected the year of the date she
   had just given, so the date is January 1, 2016.
3. Part 2 was read back while p2.lpr_date was a capture gap the app asked to
   be asked again. The lane deferred it in her name on her answer "Male.",
   which put nothing off, and that deferral settled the node for the agenda
   check.

Turn shapes are the real ones from GP's live log; the persona is swapped.
"""
import json
from unittest.mock import patch

from app.services.n400_interviewer_guard import (
    CHECKPOINT_CONFIRM_REMINDER, CHECKPOINT_REMINDER,
    REFUSED_CAPTURE_GAP_DEFERRED, REFUSED_RIDES_NEXT_QUESTION,
    checkpoint_is_refused, checkpoint_reminder, confirm_part_boundary,
    defer_dates_with_unspoken_day, guard_response_text,
)

CALL = "n400_interviewer_turn"

AGENDA = (
    "q_p2_lpr_date | Part 2: About you | p2.lpr_date | What date did you become a permanent resident?\n"
    "q_p3_ethnicity | Part 3: Your appearance | p3.ethnicity | Are you Hispanic or Latino?"
)
KNOWN = (
    "p1.first_name: John\n"
    "p1.last_name: Smith\n"
    "p2.gender: male (options: male, female)\n"
    "p2.lpr_date: said earlier, not yet recorded, ask again, partial 2016\n"
)
READBACK = ("That's Part 2: John Smith, no other names, born January 1, 1988 in Mexico, male, "
            "green card since 2016 (exact date to verify).")


def _turn(**kw):
    base = {"schema_version": 1, "turn_id": "t_008", "intent": {"type": "answer"},
            "facts": [], "deferred": [], "clarification": None, "conflict": None,
            "escalation": None, "complete": False, "asking": None,
            "section_checkpoint": None, "interview_over": False, "reply": {"en": "ok"}}
    base.update(kw)
    return json.dumps(base)


CP2_FALSE = {"part": 2, "section": "Part 2: About you", "awaiting_confirmation": False}
CP2_TRUE = {"part": 2, "section": "Part 2: About you", "awaiting_confirmation": True}
ASK_P3 = {"node_id": "q_p3_ethnicity", "field_ids": ["p3.ethnicity"]}
LPR_SELF_DEFERRED = {"field_id": "p2.lpr_date", "partial_value": "2016", "origin": "applicant",
                     "reason": "applicant gave year only, will check green card for exact date"}


# --- 1. the boundary text the client still sends ------------------------------

BOUNDARY_216 = ("the standing question is the last in Part 2 About you; when it is answered, "
                "summarize Part 2 in one sentence and open Part 3 Your appearance in the same reply, "
                "without asking for confirmation")
BOUNDARY_216_P12 = ("the standing question is the last in Part 12 Your interpreter unless her answer "
                    "opens more rows in it (such as a yes or a count above zero), which the app lists "
                    "next turn; when it is answered and opens none, summarize Part 12 in one sentence "
                    "and open Part 1 Your eligibility in the same reply, without asking for confirmation")


def test_the_walk_216_boundary_is_rewritten_to_confirm_the_part():
    out = confirm_part_boundary(CALL, BOUNDARY_216)["section_boundary"]
    assert "without asking for confirmation" not in out
    assert out == ("the standing question is the last in Part 2 About you; when it is answered, "
                   "read Part 2 back and ask if it is complete and correct (section_checkpoint "
                   "awaiting_confirmation true, asking null); Part 3 Your appearance opens only "
                   "after her yes, never in the same reply")


def test_the_conditional_boundary_keeps_its_rows_clause():
    out = confirm_part_boundary(CALL, BOUNDARY_216_P12)["section_boundary"]
    assert "unless her answer opens more rows in it" in out
    assert "when it is answered and opens none, read Part 12 back and ask if it is complete and correct" in out
    assert out.endswith("Part 1 Your eligibility opens only after her yes, never in the same reply")


def test_other_lanes_and_other_boundary_text_are_untouched():
    assert confirm_part_boundary("i765_interviewer_turn", BOUNDARY_216) == {}
    current = ("the standing question is the last in Part 1 Your eligibility; when it is answered, "
               "summarize Part 1 and ask if it is complete and correct before Part 2 About you begins")
    assert confirm_part_boundary(CALL, current) == {}
    assert confirm_part_boundary(CALL, None) == {}
    assert confirm_part_boundary(CALL, "") == {}


# --- 1. the floor when the lane rides the next question anyway ----------------

def test_the_t008_shape_is_refused():
    text = _turn(section_checkpoint=CP2_FALSE, asking=ASK_P3,
                 facts=[{"field_id": "p2.lpr_date", "value": "x"}],  # isolate from rule 3
                 reply={"en": READBACK + " Part 3 is about your appearance. To start, are you "
                                         "Hispanic or Latino, or not Hispanic or Latino?"})
    info = checkpoint_is_refused(text, AGENDA, KNOWN, "Male.")
    assert info["code"] == REFUSED_RIDES_NEXT_QUESTION and info["part"] == 2
    assert info["asking"] == "q_p3_ethnicity"
    assert checkpoint_reminder(info) == CHECKPOINT_CONFIRM_REMINDER


def test_a_read_back_that_asks_is_right_is_not_refused_even_with_asking_set():
    """Walk 216 t_011: Part 3 read back, "Is that right?", asking set. The
    client draws that as a card; it is a confirmation, not a ride-along."""
    text = _turn(section_checkpoint={"part": 3, "section": "Part 3", "awaiting_confirmation": True},
                 asking={"node_id": "q_p2_lpr_date"},
                 facts=[{"field_id": "p3.ethnicity", "value": "not_hispanic"}],
                 reply={"en": "That's Part 3: not Hispanic or Latino, White. Is that right?"})
    assert checkpoint_is_refused(text, AGENDA, KNOWN, "White") is None


def test_the_contract_shape_is_not_refused():
    text = _turn(section_checkpoint=CP2_TRUE, asking=None,
                 facts=[{"field_id": "p2.lpr_date", "value": "2016-01-01"}],
                 reply={"en": READBACK.replace(" (exact date to verify)", "") + " Is that right?",
                        "es": "Esa es la Parte 2. ¿Es correcto?"})
    assert checkpoint_is_refused(text, AGENDA, KNOWN, "2016.") is None


# --- 3. a part closed over a capture gap she never put off ---------------------

def test_a_self_made_deferral_of_a_capture_gap_does_not_close_the_part():
    text = _turn(section_checkpoint=CP2_TRUE, deferred=[LPR_SELF_DEFERRED],
                 reply={"en": READBACK + " Is that right?"})
    info = checkpoint_is_refused(text, AGENDA, KNOWN, "Male.")
    assert info["code"] == REFUSED_CAPTURE_GAP_DEFERRED
    assert info["open_nodes"] == ["q_p2_lpr_date"]
    assert checkpoint_reminder(info) == CHECKPOINT_REMINDER


def test_her_own_put_off_lets_the_deferral_close_the_part():
    text = _turn(section_checkpoint=CP2_TRUE, deferred=[LPR_SELF_DEFERRED],
                 reply={"en": READBACK + " Is that right?"})
    assert checkpoint_is_refused(text, AGENDA, KNOWN, "I don't know, I'll check my card later.") is None
    assert checkpoint_is_refused(text, AGENDA, KNOWN, "No sé, lo reviso después.") is None


def test_a_field_that_is_not_a_capture_gap_is_left_to_the_agenda_check():
    known = KNOWN.replace("p2.lpr_date: said earlier, not yet recorded, ask again, partial 2016\n", "")
    text = _turn(section_checkpoint=CP2_TRUE, deferred=[LPR_SELF_DEFERRED],
                 reply={"en": READBACK + " Is that right?"})
    assert checkpoint_is_refused(text, AGENDA, known, "Male.") is None


# --- 2. a year that corrects the date she just gave ----------------------------

CONV_YEAR_FIX = (
    "INTERVIEWER: Now, back to your Green Card: what exact date did you become a permanent resident?\n"
    "APPLICANT: January 1st, 2026.\n"
    "INTERVIEWER: I have January 1st, 2026 just now, but 2016 earlier, and 2026 hasn't happened yet. "
    "What date does your green card actually show?\n"
    "APPLICANT: 2016."
)


def _lpr_fact(value="2016-01-01", utterance="2016"):
    return {"field_id": "p2.lpr_date", "value": value, "value_type": "date",
            "provenance": {"source": "ai_extracted", "confidence": 0.9, "utterance": utterance}}


def test_the_corrected_year_keeps_her_day_and_month():
    text = _turn(facts=[_lpr_fact()], asking={"node_id": "q_p2_lpr_date"},
                 reply={"en": "January 1st, 2016, got it."})
    out, moved = defer_dates_with_unspoken_day(text, "2016.", KNOWN, CONV_YEAR_FIX)
    assert moved == []
    assert [f["value"] for f in json.loads(out)["facts"]] == ["2016-01-01"]


def test_the_corrected_year_survives_the_whole_guard():
    text = _turn(facts=[_lpr_fact()], reply={"en": "January 1st, 2016, got it."})
    out = json.loads(guard_response_text(text, AGENDA, "t_014", user_content="2016.",
                                         conversation=CONV_YEAR_FIX, locale="en",
                                         known_facts=KNOWN))
    assert [(f["field_id"], f["value"]) for f in out["facts"]] == [("p2.lpr_date", "2016-01-01")]
    assert not out.get("deferred")


def test_a_year_alone_after_a_year_alone_is_still_only_a_year():
    conv = CONV_YEAR_FIX.replace("APPLICANT: January 1st, 2026.", "APPLICANT: 2026.")
    text = _turn(facts=[_lpr_fact()], reply={"en": "January 1st, 2016, got it."})
    out, moved = defer_dates_with_unspoken_day(text, "2016.", KNOWN, conv)
    assert [m["field_id"] for m in moved] == ["p2.lpr_date"]


def test_a_different_day_is_not_carried_by_the_correction():
    text = _turn(facts=[_lpr_fact("2016-01-15")], reply={"en": "January 15th, 2016, got it."})
    out, moved = defer_dates_with_unspoken_day(text, "2016.", KNOWN, CONV_YEAR_FIX)
    assert [m["field_id"] for m in moved] == ["p2.lpr_date"]


def test_only_her_last_line_is_corrected():
    conv = CONV_YEAR_FIX.replace(
        "APPLICANT: 2016.", "APPLICANT: I'm not sure.\nINTERVIEWER: Take your time.\nAPPLICANT: 2016.")
    text = _turn(facts=[_lpr_fact()], reply={"en": "January 1st, 2016, got it."})
    out, moved = defer_dates_with_unspoken_day(text, "2016.", KNOWN, conv)
    assert [m["field_id"] for m in moved] == ["p2.lpr_date"]


# --- through the real route ------------------------------------------------------

def _send(client, free_user, replies, **md):
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata
    sent = []

    async def fake(provider_router, request, db, settings):
        sent.append(request.user_content)
        text = replies[min(len(sent), len(replies)) - 1]
        return ChatResponse(text=text, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                            provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})
    client.app.state.rate_limiter._buckets.clear()
    body = chat_request(system_prompt="", user_content="Male.",
                        metadata=_metadata(known_facts=KNOWN, agenda=AGENDA, turn_id="t_008", **md))
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", json=body, headers={**free_user["headers"], "X-App-ID": "n400"})
    return r, sent


def test_the_route_sends_the_rewritten_boundary_to_the_model(client, free_user):
    ok = _turn(section_checkpoint=CP2_TRUE, facts=[_lpr_fact()],
               reply={"en": READBACK + " Is that right?"})
    r, sent = _send(client, free_user, [ok], section_boundary=BOUNDARY_216)
    assert r.status_code == 200 and len(sent) == 1
    assert "without asking for confirmation" not in sent[0]
    assert "read Part 2 back and ask if it is complete and correct" in sent[0]


def test_the_route_retries_a_ride_along_with_the_confirm_reminder(client, free_user):
    bad = _turn(section_checkpoint=CP2_FALSE, asking=ASK_P3, facts=[_lpr_fact()],
                reply={"en": READBACK + " Part 3 is about your appearance. Are you Hispanic or Latino?"})
    good = _turn(section_checkpoint=CP2_TRUE, facts=[_lpr_fact()],
                 reply={"en": READBACK + " Is that right?"})
    r, sent = _send(client, free_user, [bad, good])
    assert r.status_code == 200 and len(sent) == 2
    assert sent[1].endswith(CHECKPOINT_CONFIRM_REMINDER)
    served = json.loads(r.json()["text"])
    assert served["asking"] is None
    assert served["checkpoint_refused"]["code"] == REFUSED_RIDES_NEXT_QUESTION
    assert served["checkpoint_refused"]["resolved"] is True
