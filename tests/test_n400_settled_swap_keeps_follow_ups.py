"""The settled-question swap keeps a follow-up the answer opened (Steven's case
515e9bb1, 2026-11-13). t_014: "Yes, can you take out the Ottilie part?" opened
a new-name clarification and he heard the USCIS account question instead. #48:
"Yes" to trips was answered with the fee-reduction question. The node ids and
the swapped-in questions are the live log's; the pre-swap reply text is
reconstructed from the clarification and the next turn (the log keeps only the
post-guard reply), so it is the shape, not the bytes."""
import json

from app.services.n400_interviewer_guard import guard_response_text

AG_P2 = (
    "q_p2_name_change | Part 2: About you | p2.name_change_requested | Would you like to legally change your name? | options: yes, no\n"
    "q_p2_uscis_account_gate | Part 2: About you | p2.has_uscis_account | Do you have a USCIS online account number? | options: yes, no\n"
    "q_p1_dob | Part 2: About you | p1.date_of_birth | What is your date of birth?\n"
)
AG_P8 = (
    "q_p8_trips_gate | Part 8: Trips | p8.has_trips | Have you taken any trips outside the United States in the last five years? | options: yes, no\n"
    "q_p10_fee_reduction | Part 10: Fee | p10.fee_reduction_requested | Is your household income less than or equal to 400% of the Federal Poverty Guidelines? | options: yes, no\n"
)


def _fact(fid, v, said="Yes."):
    return {"field_id": fid, "value": v, "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": said}}


def _run(turn, agenda, said):
    return json.loads(guard_response_text(json.dumps(turn), agenda, "t", user_content=said, locale="en"))


def test_t014_a_clarification_the_yes_opened_is_spoken_not_swapped():
    said = "Yes, can you check out the Ottilie part?"
    ask = "What would you like your new full name to be, first, middle, and last?"
    o = _run({"facts": [_fact("p2.name_change_requested", "yes", said)],
              "asking": {"node_id": "q_p2_name_change", "field_ids": ["p2.name_change_requested"]},
              "clarification": {"field_id": "p2.new_name_middle_name", "question": {"en": ask}},
              "reply": {"en": "Got it, you'd like to change your name. " + ask}}, AG_P2, said)
    assert o["reply"]["en"].endswith(ask) and "USCIS" not in o["reply"]["en"]
    assert o["asking_dropped"]["question_kept"] == "clarification"
    assert o["asking"] is None, "the clarification binds her answer, not an agenda line"


def test_48_yes_to_trips_keeps_the_trip_question():
    ask = "Tell me about your most recent trip: where did you go, when did you leave, and when did you get back?"
    o = _run({"facts": [_fact("p8.has_trips", "yes")],
              "asking": {"node_id": "q_p8_trips_gate", "field_ids": ["p8.has_trips"]},
              "reply": {"en": "Got it. " + ask}}, AG_P8, "Yes.")
    assert o["reply"]["en"].endswith(ask) and "Poverty" not in o["reply"]["en"]
    assert o["asking_dropped"]["question_kept"] == "yes_gate_follow_up"


def test_a_yes_gate_asked_again_is_still_swapped():
    """The swap's own case survives: after "yes" the lane re-asks the gate, reworded."""
    o = _run({"facts": [_fact("p8.has_trips", "yes")],
              "asking": {"node_id": "q_p8_trips_gate", "field_ids": ["p8.has_trips"]},
              "reply": {"en": "Okay. Have you taken any trips outside the United States in the last five years?"}},
             AG_P8, "Yes.")
    assert "Poverty" in o["reply"]["en"] and o["asking"]["node_id"] == "q_p10_fee_reduction"


def test_a_no_gate_still_swaps_as_before():
    o = _run({"facts": [_fact("p8.has_trips", "no", "No.")],
              "asking": {"node_id": "q_p8_trips_gate", "field_ids": ["p8.has_trips"]},
              "reply": {"en": "Okay, no trips. Where did you go on your most recent trip?"}}, AG_P8, "No.")
    assert "Poverty" in o["reply"]["en"]


def test_a_clarification_after_a_no_is_kept_too():
    """Only the clarification rule can keep this one: the gate answer is no."""
    ask = "Just to check, did you mean no trips at all, not even a short one to Mexico?"
    o = _run({"facts": [_fact("p8.has_trips", "no", "No, well, Mexico once.")],
              "asking": {"node_id": "q_p8_trips_gate", "field_ids": ["p8.has_trips"]},
              "clarification": {"field_id": "p8.has_trips", "question": {"en": ask}},
              "reply": {"en": "Okay. " + ask}}, AG_P8, "No, well, Mexico once.")
    assert o["reply"]["en"].endswith(ask) and "Poverty" not in o["reply"]["en"]
