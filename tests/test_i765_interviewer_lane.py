"""The I-765 interviewer lane (2026-10-09): `i765_interviewer_turn` on the
slug `i765/interviewer-turn`, the SAME envelope and strict JSON response as
`n400_interviewer_turn`, so the I-765 client is the N-400 client with the
prefix swapped and any envelope difference is a defect on one side.

What the lane inherits by membership in INTERVIEWER_CALL_TYPES: no generic
streaming, the envelope backstop (extract, then retry once), the sentence
stream for speech, the one hour prompt cache, the PII leak counter and the
Spanish numeral hint. What it does NOT inherit, on purpose: the N-400
response guards, decided per guard when the I-765 brief lands.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from unittest.mock import patch

from app.models.chat import ChatResponse
from app.services.interviewer_lanes import INTERVIEWER_CALL_TYPES, is_interviewer_turn
from app.services.jwt_service import JWTService
from tests.conftest import _insert_user, chat_request

SECRET = "test-secret-key-that-is-long-enough-for-hs256-validation"
I765 = {"X-App-ID": "i765"}
CALL = "i765_interviewer_turn"
AGENDA = ("q_p1_category | Part 1: Reason for applying | p1.eligibility_category | "
          "What is your eligibility category? | options: c09, c03b, a12, c19\n"
          "q_p2_a_number | Part 2: About you | p2.a_number | What is your A-Number?\n")

ENVELOPE = {
    "schema_version": 1, "turn_id": "t-i765-1", "intent": {"type": "answer"},
    "facts": [{"field_id": "p1.eligibility_category", "value": "c09",
               "source": "applicant", "evidence": "my green card application is pending"}],
    "deferred": [], "clarification": None, "conflict": None, "escalation": None,
    "complete": True, "asking": {"node_id": "q_p2_a_number", "field_ids": ["p2.a_number"]},
    "section_checkpoint": None, "interview_over": False,
    "reply": {"en": "A pending green card application, noted. What is your A-Number?"},
}


def _metadata(**over):
    md = {"call_type": CALL, "form_code": "I-765", "jurisdiction": "US-TX", "locale": "en",
          "turn_id": "t-i765-1", "conversation": "INTERVIEWER: What is your eligibility category?",
          "known_facts": "nothing yet", "agenda": AGENDA}
    md.update(over)
    return md


def _headers(db, user_id="i765-lane-user"):
    _insert_user(db, user_id=user_id, tier="free", monthly_limit=5.0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET apple_sub = ? WHERE id = ?", (f"anonymous:{uuid.uuid4().hex}", user_id))
    conn.commit(); conn.close()
    svc = JWTService(secret=SECRET, algorithm="HS256", access_expire_minutes=60, refresh_expire_days=30)
    return {"Authorization": f"Bearer {svc.create_access_token(user_id, 'i765')}", **I765}


def _turn(client, headers, text="my green card application is pending", **over):
    body = chat_request(system_prompt="", user_content=text, metadata=_metadata(**over))
    return client.post("/v1/chat", json=body, headers=headers)


def _canned(text):
    return ChatResponse(text=text, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                        provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})


# --- registration ---------------------------------------------------------------


def test_the_call_type_is_mapped_to_its_own_slug_and_the_lane_set_names_both():
    from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG
    assert _CALL_TYPE_TO_CONFIG[CALL] == "i765/interviewer-turn"
    assert _CALL_TYPE_TO_CONFIG["n400_interviewer_turn"] == "n400/interviewer-turn"
    assert INTERVIEWER_CALL_TYPES == frozenset({"n400_interviewer_turn", CALL})
    assert is_interviewer_turn(CALL) and not is_interviewer_turn("n400_interview_turn")


def test_the_config_has_the_n400_wire_shape_and_an_i765_brief():
    i765 = json.load(open("config/remote/i765/interviewer-turn.json"))
    n400 = json.load(open("config/remote/n400/interviewer-turn.json"))
    assert i765["requiredVariables"] == n400["requiredVariables"]
    assert i765["optionalVariables"] == n400["optionalVariables"]
    assert i765["userPromptTemplate"] == n400["userPromptTemplate"], "the client fills one template"
    assert i765["maxTokens"] == n400["maxTokens"] and i765["thinking"] == n400["thinking"]
    assert i765["recommendedModel"] == n400["recommendedModel"]
    assert i765["server_only"] is True and i765["version"] == 1
    sp = i765["systemPrompt"]
    assert "Form I-765" in sp and "N-400" not in sp
    assert "never pick a category" in sp and "escalation" in sp
    for key in ('"schema_version"', '"intent"', '"facts"', '"asking"', '"section_checkpoint"', '"interview_over"', '"reply"'):
        assert key in sp, key
    assert sp.index('"asking"') < sp.index('"reply"'), "decisions before the spoken line, as n400"
    assert chr(0x2014) not in sp and chr(0x2013) not in sp, "no dashes in anything the model copies"


def test_the_dial_routes_the_lane_to_sonnet_for_every_tier():
    from types import SimpleNamespace
    from app.routers.chat import _resolve_model_routing
    routing = json.load(open("config/remote/model-routing.json"))
    fallback = SimpleNamespace(default_model="anthropic/should-not-be-used")
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(remote_configs={"model-routing": routing})),
                          state=SimpleNamespace(app_id="i765"))
    body = SimpleNamespace(get_meta=lambda k: {"call_type": CALL}.get(k), metadata={"call_type": CALL})
    for tier in ("free", "plus", "pro", "automation", "anything"):
        assert _resolve_model_routing(req, body, fallback, tier) == "anthropic/claude-sonnet-5", tier


def test_the_lane_shares_the_one_hour_cache_the_leak_counter_and_the_numeral_hint():
    from app.services.providers.anthropic import _ONE_HOUR_CACHE_CALL_TYPES
    from app.services.n400_pii_leak import report
    from app.services.spanish_numerals import numeral_variables
    assert CALL in _ONE_HOUR_CACHE_CALL_TYPES and "n400_interviewer_turn" in _ONE_HOUR_CACHE_CALL_TYPES
    src = {"user_content": "my number is 123 45 6789", "conversation": "", "known_facts": ""}
    leak = report(CALL, src)
    assert leak == report("n400_interviewer_turn", src)
    assert leak, "the counter runs on the I-765 lane"
    assert report("i765_something_else", src) == []
    es = numeral_variables(CALL, "es", "nací en mil novecientos noventa")
    assert es == numeral_variables("n400_interviewer_turn", "es", "nací en mil novecientos noventa") and es
    assert numeral_variables(CALL, "en", "nineteen ninety") == {}


# --- the turn -----------------------------------------------------------------


def test_a_turn_is_assembled_from_the_i765_brief_and_answered_as_the_object(client, tmp_db_path, mock_provider):
    h = _headers(tmp_db_path)
    mock_provider.return_value = _canned(json.dumps(ENVELOPE))
    r = _turn(client, h)
    assert r.status_code == 200, r.text
    obj = json.loads(r.json()["text"])
    assert obj["reply"]["en"].startswith("A pending green card")
    assert obj["asking"]["node_id"] == "q_p2_a_number"
    assert mock_provider.call_count == 1, "an object needs no retry"
    sent = mock_provider.call_args.args[0] if mock_provider.call_args.args else mock_provider.call_args.kwargs["request"]
    assert "Form I-765" in sent.system_prompt and "N-400" not in sent.system_prompt
    assert sent.max_tokens == 6144
    assert "AGENDA" in sent.user_content and "q_p1_category" in sent.user_content


def test_a_missing_required_variable_is_refused_not_assembled_as_another_lane(client, tmp_db_path):
    h = _headers(tmp_db_path)
    body = chat_request(system_prompt="", user_content="hi", metadata=_metadata())
    del body["metadata"]["agenda"]
    r = client.post("/v1/chat", json=body, headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "missing_prompt_variables"
    assert r.json()["detail"]["details"]["call_type"] == CALL


def test_prose_is_retried_once_and_a_preamble_is_extracted(client, tmp_db_path, mock_provider):
    h = _headers(tmp_db_path)
    mock_provider.return_value = _canned("Sure, your category is pending adjustment. What is your A-Number?")
    r = _turn(client, h)
    assert r.status_code == 200
    assert mock_provider.call_count == 2, "exactly one retry, as on the N-400 lane"
    mock_provider.reset_mock()
    mock_provider.return_value = _canned("Noted.\n\n" + json.dumps(ENVELOPE))
    r = _turn(client, h, turn_id="t-i765-2")
    assert mock_provider.call_count == 1
    assert json.loads(r.json()["text"])["envelope_extracted"] is True


def test_a_stream_request_takes_the_sentence_stream_not_the_generic_one(client, tmp_db_path):
    h = _headers(tmp_db_path)
    text = json.dumps(ENVELOPE, ensure_ascii=False)

    async def gen(provider_router, request, db, settings):
        for i in range(0, len(text), 9):
            yield {"type": "text", "text": text[i:i + 9]}
        yield {"type": "text", "text": "", "done": True, "ttft_ms": 210, "response": _canned(text)}

    body = chat_request(system_prompt="", user_content="my green card application is pending",
                        stream=True, metadata=_metadata())
    with patch("app.services.anthropic_or_fallback.route_stream_with_fallback", gen):
        with client.stream("POST", "/v1/chat", json=body, headers=h) as r:
            assert r.status_code == 200, r.read()
            assert r.headers["content-type"].startswith("text/event-stream")
            raw = r.read().decode()
    events = [b.split("\n")[0][len("event: "):] for b in raw.strip().split("\n\n")]
    assert "sentence" in events and events[-1] == "envelope", events
    assert "error" not in events
    env = json.loads(next(b for b in raw.strip().split("\n\n") if b.startswith("event: envelope")).split("\n")[1][len("data: "):])
    assert json.loads(env["text"])["reply"]["en"] == ENVELOPE["reply"]["en"]
