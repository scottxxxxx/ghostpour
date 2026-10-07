"""A row never ends before it starts (the auditor, 2026-10-07).

Scott's comparison run 3, 03:19:01Z: KNOWN FACTS held p4.prior_address1.to
= 2019-03-02 and .from deferred with a partial 2016-08. She said "March 3rd,
2019" and the lane filed p4.prior_address1.from = 2019-03-03, so the prior
address ended before it started and the 2016-08 was overwritten. The lane
had named that very mismatch four minutes earlier.

Partials are read in her favor: an inversion counts only when it is certain
(the earliest possible start after the latest possible end). "present",
deferred notes and unreadable values never count.
"""
import json
import sqlite3
from unittest.mock import patch

from app.services.n400_interviewer_guard import (
    date_inversion_reminder, date_inversions, drop_inverted_dates, mark_date_inversion,
)

RUN3_KNOWN = (
    "p4.has_prior_address1: yes (options: yes, no)\n"
    "p4.prior_address1.street: 481 Bayberry Street\n"
    "p4.prior_address1.city: Dallas\n"
    "p4.prior_address1.to: 2019-03-02\n"
    "p4.prior_address1.from: deferred, to verify from a document, partial 2016-08\n"
    "p4.prior_address1.state: TX\n"
)


def _turn(*facts, reply="March 3rd, 2019, noted."):
    return json.dumps({
        "schema_version": 1, "turn_id": "t_039", "intent": {"type": "answer"},
        "facts": [{"field_id": f, "value": v, "value_type": "string"} for f, v in facts],
        "deferred": [], "clarification": None, "conflict": None, "escalation": None,
        "complete": False, "asking": None, "section_checkpoint": None,
        "interview_over": False, "reply": {"en": reply},
    })


# --- detection ---------------------------------------------------------------

def test_the_run_3_move_in_is_refused_against_the_move_out_on_file():
    inv = date_inversions(_turn(("p4.prior_address1.from", "2019-03-03")), RUN3_KNOWN)
    assert [(i["field_id"], i["other_field_id"], i["other_value"], i["other_source"]) for i in inv] == [
        ("p4.prior_address1.from", "p4.prior_address1.to", "2019-03-02", "on_file")]


def test_a_pair_minted_together_is_checked_against_itself():
    inv = date_inversions(_turn(("p7.employer2.from", "2022-03-01"),
                                ("p7.employer2.to", "2021-03-01")), "")
    assert {i["field_id"] for i in inv} == {"p7.employer2.from", "p7.employer2.to"}
    assert all(i["other_source"] == "this_answer" for i in inv)


def test_the_same_answer_wins_over_the_file():
    """She corrects both ends at once: the on-file .to is stale, the new pair
    is the one that gets filed, and it is in order."""
    known = "p7.employer1.to: 2015-01-01\n"
    assert date_inversions(_turn(("p7.employer1.from", "2016-02-01"),
                                 ("p7.employer1.to", "2018-02-01")), known) == []


def test_a_partial_is_read_in_her_favor():
    """from 2019-03 can mean March 1st, which is not after March 2nd."""
    known = "p4.prior_address1.to: 2019-03-02\n"
    assert date_inversions(_turn(("p4.prior_address1.from", "2019-03")), known) == []
    assert date_inversions(_turn(("p4.prior_address1.to", "2016")),
                           "p4.prior_address1.from: 2016-08-01\n") == []


def test_present_deferred_and_unreadable_never_count():
    assert date_inversions(_turn(("p7.employer1.from", "2019-03-15")),
                           "p7.employer1.to: present\n") == []
    # the on-file FROM in run 3 is a deferral note: a new .to is not checked against it
    assert date_inversions(_turn(("p4.prior_address1.to", "2010-01-01")), RUN3_KNOWN) == []
    assert date_inversions(_turn(("p4.prior_address1.from", "around then")), RUN3_KNOWN) == []


def test_an_ordered_row_and_a_row_with_no_other_side_pass():
    assert date_inversions(_turn(("p4.prior_address1.from", "2016-08-01")), RUN3_KNOWN) == []
    assert date_inversions(_turn(("p4.prior_address2.from", "2030-01-01")), RUN3_KNOWN) == []


def test_non_date_rows_are_ignored():
    assert date_inversions(_turn(("p8.trip1.date_left", "2025-06-24"),
                                 ("p8.trip1.date_returned", "2025-06-10")), "") == []


# --- what the refusal does ------------------------------------------------------

def test_the_reminder_names_both_dates_and_asks():
    inv = date_inversions(_turn(("p4.prior_address1.from", "2019-03-03")), RUN3_KNOWN)
    r = date_inversion_reminder(inv)
    assert "p4.prior_address1.from = 2019-03-03" in r and "p4.prior_address1.to = 2019-03-02" in r
    assert "ask her which one is right" in r and "Do not file either date" in r


def test_dropping_removes_only_the_inverted_facts():
    text = _turn(("p4.prior_address1.from", "2019-03-03"), ("p7.employer2.from", "2015-08-01"))
    inv = date_inversions(text, RUN3_KNOWN)
    left = json.loads(drop_inverted_dates(text, inv))["facts"]
    assert [f["field_id"] for f in left] == ["p7.employer2.from"]
    marked = json.loads(mark_date_inversion(text, inv, retried=True, resolved=False))
    assert marked["date_inversion_refused"]["resolved"] is False


# --- through the real route -----------------------------------------------------

def _statuses(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT status FROM usage_log WHERE app_id='n400' ORDER BY rowid")]
    finally:
        conn.close()


def _send(client, free_user, replies):
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
    body = chat_request(system_prompt="", user_content="March 3rd, 2019.",
                        metadata=_metadata(known_facts=RUN3_KNOWN, turn_id="t-inv"))
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", json=body, headers={**free_user["headers"], "X-App-ID": "n400"})
    return r, sent


def test_the_route_retries_once_and_serves_the_corrected_turn(client, free_user, tmp_db_path):
    bad = _turn(("p4.prior_address1.from", "2019-03-03"))
    good = _turn(reply="You moved out on March 2nd, 2019, and March 3rd, 2019 comes after that. Which is right?")
    r, sent = _send(client, free_user, [bad, good])
    assert r.status_code == 200
    served = json.loads(r.json()["text"])
    assert served["facts"] == []
    assert served["date_inversion_refused"]["resolved"] is True
    assert len(sent) == 2 and "Do not file either date" in sent[1]
    assert "date_inversion_retry" in _statuses(tmp_db_path)


def test_a_retry_that_still_inverts_is_dropped_never_filed(client, free_user, tmp_db_path):
    bad = _turn(("p4.prior_address1.from", "2019-03-03"))
    r, sent = _send(client, free_user, [bad, bad])
    assert r.status_code == 200
    served = json.loads(r.json()["text"])
    assert all(f["field_id"] != "p4.prior_address1.from" for f in served["facts"])
    assert served["date_inversion_refused"]["resolved"] is False
    assert len(sent) == 2
    assert {"date_inversion_retry", "date_inversion_retry_failed"} <= set(_statuses(tmp_db_path))


def test_an_ordered_turn_is_not_retried(client, free_user):
    r, sent = _send(client, free_user, [_turn(("p4.prior_address1.from", "2016-08-01"))])
    assert r.status_code == 200 and len(sent) == 1
    assert "date_inversion_refused" not in json.loads(r.json()["text"])
