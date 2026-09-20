"""The interviewer lane's sentence stream, on the wire.

What this proves that the module tests cannot: the streamed turn goes through
the same tail as the JSON path (a stale `asking` is dropped by the guard and
the envelope carries the marker), the last sentence rides the envelope rather
than a sentence event, the envelope body is the JSON path's body, and the
usage row records a time to first token for the first time on this lane.
"""
import json
from unittest.mock import patch

import pytest

from app.models.chat import ChatResponse

CALL = "n400_interviewer_turn"
AGENDA = ("q_p1_eligibility_basis | Part 1: Eligibility | p1.eligibility_basis | Why are you eligible? | options: general_provision, spouse_usc\n"
          "q_p1_a_number | Part 1: Eligibility | p1.a_number | What is your A-Number?\n")

ENVELOPE = {
    "schema_version": 1, "turn_id": "t-stream-1", "intent": "answer",
    "facts": [{"field_id": "p1.eligibility_basis", "value": "general_provision",
               "source": "applicant", "evidence": "on my own, about six years"}],
    "deferred": [], "clarification": None, "conflict": None, "escalation": None,
    "complete": True,
    # NOT on the agenda: drop_stale_asking must remove it. If the envelope
    # still carries it, the guards did not run on the streamed turn.
    "asking": {"node_id": "q_nowhere_on_the_agenda"},
    "section_checkpoint": None, "interview_over": False,
    "reply": {"en": "Six years on your own, noted. That's the general five year path. Now, what is your A-Number?"},
}


def _metadata(**over):
    md = {"call_type": CALL, "form_code": "N-400", "jurisdiction": "US",
          "locale": "en", "turn_id": "t-stream-1",
          "conversation": "Interviewer: Why do you believe you are eligible?",
          "known_facts": "p2.country_of_birth = Mexico", "agenda": AGENDA}
    md.update(over)
    return md


def _stream_of(text: str, chunk: int = 7):
    """A provider stream: the text in small deltas, then the done event with
    the assembled ChatResponse, exactly the adapter's contract."""
    async def gen(provider_router, request, db, settings):
        for i in range(0, len(text), chunk):
            yield {"type": "text", "text": text[i:i + chunk]}
        yield {"type": "text", "text": "", "done": True, "ttft_ms": 321,
               "response": ChatResponse(text=text, input_tokens=100, output_tokens=50,
                                        model="claude-sonnet-5", provider="anthropic",
                                        usage={"input_tokens": 100, "output_tokens": 50})}
    return gen


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.strip().split("\n\n"):
        lines = block.split("\n")
        ev = next(l[len("event: "):] for l in lines if l.startswith("event: "))
        data = next(l[len("data: "):] for l in lines if l.startswith("data: "))
        out.append((ev, json.loads(data)))
    return out


def _post(client, free_user, **body_over):
    from tests.conftest import chat_request
    body = chat_request(system_prompt="", user_content="on my own, about six years",
                        stream=True, metadata=_metadata())
    body.update(body_over)
    with client.stream("POST", "/v1/chat", json=body,
                       headers={**free_user["headers"], "X-App-ID": "n400"}) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        return _events(r.read().decode())


def test_sentences_stream_early_and_the_envelope_is_guarded(client, free_user, tmp_db_path):
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    with patch("app.services.anthropic_or_fallback.route_stream_with_fallback", _stream_of(text)):
        events = _post(client, free_user)

    kinds = [e for e, _ in events]
    sentences = [d for e, d in events if e == "sentence"]
    envelopes = [d for e, d in events if e == "envelope"]
    assert kinds.count("envelope") == 1 and kinds[-1] == "envelope"
    assert not any(e == "error" for e in kinds)

    # All but the last sentence, in order, zero based and gapless.
    assert [s["text"] for s in sentences] == [
        "Six years on your own, noted.", "That's the general five year path."]
    assert [s["index"] for s in sentences] == [0, 1]
    assert all(s["locale"] == "en" for s in sentences)

    env = envelopes[0]
    st = env["stream"]
    assert st["sentences_released"] == 2
    assert st["held_sentence"] == {"index": 2, "locale": "en", "text": "Now, what is your A-Number?"}
    assert st["spoken_prefix_stale"] is False
    assert st["buffered"] is None

    # The envelope is the JSON path's body: `text` is the whole guarded object.
    obj = json.loads(env["text"])
    assert obj["reply"]["en"] == ENVELOPE["reply"]["en"], "reply is still the WHOLE reply"
    assert obj["reply"]["en"].endswith(st["held_sentence"]["text"]), "held sentence is its suffix"
    # THE GUARDS RAN ON THE STREAMED TURN: the stale asking was dropped.
    assert obj["asking"] is None
    assert "asking_dropped" in obj

    # And the lane records a time to first token for the first time.
    import sqlite3
    row = sqlite3.connect(tmp_db_path).execute(
        "SELECT ttft_ms, status FROM usage_log WHERE call_type=? ORDER BY id DESC LIMIT 1", (CALL,)
    ).fetchone()
    assert row == (321, "success")


def test_reply_before_the_keys_buffers_and_still_delivers_the_envelope(client, free_user):
    """The safe failure: nothing streams, the client gets today's behaviour
    plus the reason."""
    order = ["schema_version", "turn_id", "reply", "intent", "facts", "deferred",
             "clarification", "conflict", "escalation", "complete", "asking",
             "section_checkpoint", "interview_over"]
    text = json.dumps({k: ENVELOPE[k] for k in order}, ensure_ascii=False)
    with patch("app.services.anthropic_or_fallback.route_stream_with_fallback", _stream_of(text)):
        events = _post(client, free_user)
    assert [e for e, _ in events if e == "sentence"] == []
    env = next(d for e, d in events if e == "envelope")
    assert env["stream"]["buffered"] == "reply_before_section_checkpoint,facts,deferred"
    assert env["stream"]["held_sentence"] is None
    assert json.loads(env["text"])["reply"]["en"] == ENVELOPE["reply"]["en"]


def test_an_oath_node_on_the_agenda_streams_nothing(client, free_user):
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    oath_agenda = AGENDA + "q_p9_oath | Part 9: Oath | p9.willing_bear_arms | Are you willing to bear arms?\n"
    from tests.conftest import chat_request
    body = chat_request(system_prompt="", user_content="yes", stream=True,
                        metadata=_metadata(agenda=oath_agenda))
    with patch("app.services.anthropic_or_fallback.route_stream_with_fallback", _stream_of(text)):
        with client.stream("POST", "/v1/chat", json=body,
                           headers={**free_user["headers"], "X-App-ID": "n400"}) as r:
            events = _events(r.read().decode())
    assert [e for e, _ in events if e == "sentence"] == []
    assert next(d for e, d in events if e == "envelope")["stream"]["buffered"] == "oath_node_on_agenda"


def test_without_the_flag_the_lane_is_the_json_path_it_always_was(client, free_user):
    """stream false (or absent) changes nothing: a plain JSON body, no SSE."""
    from tests.conftest import chat_request
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    body = chat_request(system_prompt="", user_content="on my own, about six years",
                        metadata=_metadata())
    resp = ChatResponse(text=text, input_tokens=100, output_tokens=50,
                        model="claude-sonnet-5", provider="anthropic",
                        usage={"input_tokens": 100, "output_tokens": 50})

    async def fake(provider_router, request, db, settings):
        return resp
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        r = client.post("/v1/chat", json=body,
                        headers={**free_user["headers"], "X-App-ID": "n400"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert "stream" not in r.json()


def test_a_provider_failure_after_release_is_an_error_event_that_names_the_count(client, free_user):
    """Auditor ruling 4: the client applies nothing and charges nothing; it
    needs to know how many sentences she already heard."""
    text = json.dumps(ENVELOPE, ensure_ascii=False)
    cut = text.index("Now, what is")   # after two sentences have been released

    async def gen(provider_router, request, db, settings):
        for i in range(0, cut, 7):
            yield {"type": "text", "text": text[i:i + 7]}
        raise RuntimeError("upstream died mid-stream")
    with patch("app.services.anthropic_or_fallback.route_stream_with_fallback", gen):
        events = _post(client, free_user)
    kinds = [e for e, _ in events]
    assert "envelope" not in kinds
    err = next(d for e, d in events if e == "error")
    assert err["sentences_released"] == kinds.count("sentence") == 2
    assert err["code"] == "provider_error"
