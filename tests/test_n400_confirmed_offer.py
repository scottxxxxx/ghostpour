"""v62: a value the lane offered and she confirmed with a yes (2026-10-14).

The lane may read a likely value back as a question and mint it on her yes,
citing the yes. The evidence floor already keeps such a fact (her yes IS in
what she just said); the day guard did not, because a yes holds no day, so a
confirmed "So May 1, 2025?" was deferred with nothing kept.
"""

import json

from app.services.n400_interviewer_guard import guard_response_text

AGENDA = "q_p4_current_from | Part 4 | p4.current_address.from | When did you move in?"


def _guard(value, said, conversation, known=""):
    turn = json.dumps({"schema_version": 1, "turn_id": "t_9", "intent": {"type": "answer"},
                       "facts": [{"field_id": "p4.current_address.from", "value": value,
                                  "value_type": "date",
                                  "provenance": {"source": "ai_extracted", "turn_id": "t_9",
                                                 "utterance": said}}],
                       "deferred": [], "asking": None, "reply": {"en": "Okay."}, "complete": True})
    out = json.loads(guard_response_text(turn, AGENDA, "t_9", user_content=said,
                                         conversation=conversation, locale="en", known_facts=known))
    return ({f["field_id"]: f["value"] for f in out.get("facts", [])},
            {d["field_id"]: d.get("partial_value") for d in out.get("deferred", [])})


def _conv(hers, asked, yes):
    return f"APPLICANT: {hers}\nINTERVIEWER: {asked}\nAPPLICANT: {yes}"


def test_a_date_she_confirmed_is_kept():
    c = _conv("I moved in May 1 last year.", "May 1 last year, so May 1, 2025?", "Yes, that's right.")
    assert _guard("2025-05-01", "Yes, that's right.", c) == ({"p4.current_address.from": "2025-05-01"}, {})


def test_spanish_confirmation_with_the_year_read_in_words():
    c = _conv("Me mudé el primero de mayo del año pasado.",
              "¿El primero de mayo de dos mil veinticinco?", "Sí, correcto.")
    assert _guard("2025-05-01", "Sí, correcto.", c)[0] == {"p4.current_address.from": "2025-05-01"}


def test_a_day_she_never_said_is_not_confirmed_into_a_fact():
    c = _conv("I moved in May last year.", "So May 1, 2025?", "Yes.")
    facts, deferred = _guard("2025-05-01", "Yes.", c)
    assert facts == {} and "p4.current_address.from" in deferred


def test_a_day_on_file_may_be_offered():
    c = _conv("Then I moved here.", "You moved here on March 3, 2023, the day you left Elm Street?", "Yes.")
    known = "p4.prior_address1.to: 2023-03-03"
    assert _guard("2023-03-03", "Yes.", c, known)[0] == {"p4.current_address.from": "2023-03-03"}


def test_the_question_must_have_read_that_date():
    c = _conv("I moved in May 1 last year.", "So May 2, 2025?", "Yes.")
    assert _guard("2025-05-01", "Yes.", c)[0] == {}


def test_a_statement_is_not_an_offer():
    c = _conv("I moved in May 1 last year.", "I have May 1, 2025.", "Yes.")
    assert _guard("2025-05-01", "Yes.", c)[0] == {}


def test_a_yes_that_carries_its_own_date_is_not_a_bare_confirmation():
    c = _conv("I moved in May 1 last year.", "So May 1, 2025?", "Yes, in June actually.")
    assert _guard("2025-05-01", "Yes, in June actually.", c)[0] == {}


def test_english_read_back_with_the_year_in_words():
    c = _conv("I moved in May 1 last year.", "May first, twenty twenty-five?", "Yeah.")
    assert _guard("2025-05-01", "Yeah.", c)[0] == {"p4.current_address.from": "2025-05-01"}


# --- the trip home: the country offered, minted on her yes (2026-10-15) ---

TRIP_AGENDA = "q_p8_trip1 | Part 8 | p8.trip1.countries,p8.trip1.date_left,p8.trip1.date_returned | Where did you go?"


def _trip(said, conversation, cited=None):
    turn = json.dumps({"schema_version": 1, "turn_id": "t_9", "intent": {"type": "answer"},
                       "facts": [{"field_id": "p8.trip1.countries", "value": "Mexico", "value_type": "text",
                                  "provenance": {"source": "ai_extracted", "turn_id": "t_9",
                                                 "utterance": cited or said}}],
                       "deferred": [], "asking": None, "reply": {"en": "Okay."}, "complete": False})
    out = json.loads(guard_response_text(turn, TRIP_AGENDA, "t_9", user_content=said,
                                         conversation=conversation, locale="en",
                                         known_facts="p2.country_of_birth: Mexico"))
    return {f["field_id"]: f["value"] for f in out.get("facts", [])}


def test_a_trip_country_confirmed_with_a_bare_yes_is_kept():
    c = ("APPLICANT: I went home for Christmas last year, about two weeks.\n"
         "INTERVIEWER: Christmas last year, so December 2025. Was that home to Mexico, and what day did you leave?\n"
         "APPLICANT: Yes.")
    assert _trip("Yes.", c) == {"p8.trip1.countries": "Mexico"}


def test_a_spanish_si_keeps_it_too():
    c = ("APPLICANT: Volví a mi país en Navidad del año pasado.\n"
         "INTERVIEWER: ¿Fue a México, y qué día salió?\n"
         "APPLICANT: Sí.")
    assert _trip("Sí.", c) == {"p8.trip1.countries": "Mexico"}


def test_a_confirmed_country_citing_her_earlier_line_is_dropped():
    """The yes is the evidence; her "went home" line never said Mexico."""
    c = ("APPLICANT: I went home for Christmas last year, about two weeks.\n"
         "INTERVIEWER: Was that home to Mexico?\n"
         "APPLICANT: Yes.")
    assert _trip("Yes.", c, cited="I went home for Christmas last year, about two weeks.") == {}
