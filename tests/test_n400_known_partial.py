"""A date filed off its own known partial (2026-10-15, build 182 revisit).

She gave "November 2021", tapped Answer by voice, heard "What day in
November?" and said "The 15th." The day guard checks only the day, so a
2021-12-15 assembled from that same line was kept. With the partial kept in
KNOWN FACTS, a minted date must start with it unless she says the month or
year anew in this line, which is her correction.
"""
import json

from app.services.n400_interviewer_guard import guard_response_text

ASK = "INTERVIEWER: Sure, let's answer this one now. You moved into 7240 Tamarack Road in November 2021. What day in November?"
AGENDA = "q_p4_prior_address1 | Part 4 | p4.prior_address1.from,p4.prior_address1.to | What day?"
REVISIT = "p4.prior_address1.street: 7240 Tamarack Road\np4.prior_address1.from: 2021-11, day missing"


def _guard(facts, said, known, ask=ASK):
    turn = json.dumps({"schema_version": 1, "turn_id": "t_9", "intent": {"type": "answer"},
                       "facts": [{"field_id": f, "value": v, "value_type": "date",
                                  "provenance": {"source": "ai_extracted", "turn_id": "t_9", "utterance": said}}
                                 for f, v in facts],
                       "deferred": [], "asking": None, "reply": {"en": "Okay."}, "complete": True})
    out = json.loads(guard_response_text(turn, AGENDA, "t_9", user_content=said,
                                         conversation=f"{ask}\nAPPLICANT: {said}", locale="en",
                                         known_facts=known))
    return ({f["field_id"]: f["value"] for f in out.get("facts", [])},
            {d["field_id"]: d.get("partial_value") for d in out.get("deferred", [])})


F = "p4.prior_address1.from"


def test_her_day_on_the_partial_is_kept():
    for said in ("The 15th.", "15", "November 15th"):
        assert _guard([(F, "2021-11-15")], said, REVISIT) == ({F: "2021-11-15"}, {}), said


def test_a_month_she_did_not_say_is_refused_and_deferred_back_with_the_partial():
    assert _guard([(F, "2021-12-15")], "The 15th.", REVISIT) == ({}, {F: "2021-11"})


def test_a_year_she_did_not_say_is_refused():
    assert _guard([(F, "2020-11-15")], "The 15th.", REVISIT) == ({}, {F: "2021-11"})


def test_a_year_or_month_she_says_now_is_her_correction():
    known = "p7.employer1.from: deferred, to verify from a document, partial 2018"
    assert _guard([("p7.employer1.from", "2019-03-03")], "Actually it was March 3, 2019.", known)[0] == \
        {"p7.employer1.from": "2019-03-03"}
    assert _guard([("p7.employer1.from", "2019-03-03")], "It was March 3rd, twenty nineteen.", known)[0] == \
        {"p7.employer1.from": "2019-03-03"}
    assert _guard([(F, "2021-12-15")], "Oh wait, December 15th.", REVISIT)[0] == {F: "2021-12-15"}


def test_the_deferral_shape_and_a_year_only_partial_are_read_too():
    known = "p4.current_address.from: deferred, to verify from a document, partial 2020-06"
    assert _guard([("p4.current_address.from", "2020-07-04")], "The 4th.", known) == \
        ({}, {"p4.current_address.from": "2020-06"})
    lpr = "p2.lpr_date: 2017, month and day missing"
    assert _guard([("p2.lpr_date", "2017-06-02")], "June 2nd.", lpr)[0] == {"p2.lpr_date": "2017-06-02"}
    assert _guard([("p2.lpr_date", "2018-06-02")], "June 2nd.", lpr) == ({}, {"p2.lpr_date": "2017"})


def test_inert_without_a_partial():
    assert _guard([(F, "2021-12-15")], "The 15th.", "p4.prior_address1.street: 7240 Tamarack Road")[0] == \
        {F: "2021-12-15"}
    # a FILED full date on file is not a partial either
    assert _guard([(F, "2021-12-15")], "The 15th.", "p4.prior_address1.from: 2019-03-04")[0] == \
        {F: "2021-12-15"}
