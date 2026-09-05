"""The envelope backstop: what counts as the object, and that the route retries once."""

import json

from app.services.n400_envelope import ENVELOPE_REMINDER, is_envelope, mark_retried


def test_the_turn_76_shape_is_not_an_envelope():
    prose = ("All facts for Part 9 are already recorded, so that yes just confirms the summary. "
             "Now let's do a full read-back of everything, part by part. Does all of that sound complete and correct?")
    assert not is_envelope(prose)


def test_the_object_with_a_reply_is_an_envelope_and_others_are_not():
    assert is_envelope(json.dumps({"schema_version": 1, "reply": {"en": "x"}, "facts": []}))
    assert not is_envelope(json.dumps({"schema_version": 1, "facts": []}))
    assert not is_envelope(json.dumps([{"reply": {"en": "x"}}]))
    assert not is_envelope("")
    assert not is_envelope(None)


def test_a_successful_retry_is_marked_visibly():
    out = json.loads(mark_retried(json.dumps({"reply": {"en": "x"}, "facts": []})))
    assert out["envelope_retried"] is True and out["reply"] == {"en": "x"}
    assert mark_retried("still prose") == "still prose"


def test_the_reminder_says_what_went_wrong_and_what_to_return():
    assert "prose" in ENVELOPE_REMINDER and "JSON object" in ENVELOPE_REMINDER and "read-back" in ENVELOPE_REMINDER


def test_the_route_retries_exactly_once_for_this_call_type_and_meters_the_discard():
    src = open("app/routers/chat.py").read()
    i = src.index("from app.services.n400_envelope import")
    block = src[i - 400:i + 2200]
    assert 'body.get_meta("call_type") == "n400_interviewer_turn"' in block
    assert block.count("await route_with_fallback(") == 1, "exactly one retry"
    assert 'status="envelope_retry"' in block and 'status="envelope_retry_failed"' in block
    assert "n400_envelope_retried" in block and "n400_envelope_prose" in block
