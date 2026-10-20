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
# The brief's section 4.1 lines (ids, titles and options are the catalog's;
# the ask text is shortened here, the client sends the full text).
AGENDA = ("q_p1_reason | Part 1: Why you are applying | p1.reason | Is this your first work permit, "
          "a replacement, or a renewal? | options: initial, replacement, renewal\n"
          "q_p2_eligibility_category | Part 2: About you | p2.eligibility_category | Which eligibility "
          "category is on your paperwork? | options: c9, c3a, c3b, c3c, a12, c19, a17, a18, a3, a5, c26, other\n"
          "q_p2_full_name | Part 2: About you | p2.given_name,p2.middle_name,p2.family_name | What is your full legal name?\n")
BOUNDARY = ("the standing question is the last in Part 1 Why you are applying; when it is answered, "
            "summarize Part 1 in one sentence and open Part 2 About you in the same reply, without asking for confirmation")

# The served N-400 shape: `intent` as the object (the I-765 client decodes both
# shapes, the lead confirmed 2026-10-09), value_type + provenance.utterance on facts.
ENVELOPE = {
    "schema_version": 1, "turn_id": "t-i765-1", "intent": {"type": "answer"},
    "facts": [{"field_id": "p1.reason", "value": "initial", "value_type": "string",
               "provenance": {"source": "user_stated", "confidence": 0.96,
                              "utterance": "my first work permit"}}],
    "deferred": [], "clarification": None, "conflict": None, "escalation": None,
    "complete": True,
    "asking": {"node_id": "q_p2_eligibility_category", "field_ids": ["p2.eligibility_category"]},
    "section_checkpoint": {"part": 1, "section": "Part 1: Why you are applying", "awaiting_confirmation": False},
    "interview_over": False,
    "reply": {"en": "A first permit. Now the one thing that decides the rest of the form: which eligibility category is on your paperwork?"},
}


def _metadata(**over):
    md = {"call_type": CALL, "form_code": "I-765", "jurisdiction": "US-TX", "locale": "en",
          "turn_id": "t-i765-1",
          "conversation": "INTERVIEWER: Is this your first work permit, a replacement, or a renewal?\nAPPLICANT: my first work permit",
          "known_facts": "p2.mailing_address.state: TX", "agenda": AGENDA, "section_boundary": BOUNDARY,
          "applicant_context": "state: Texas; language: English; interpreter: no; filing for self: yes"}
    md.update(over)
    return md


def _headers(db, user_id="i765-lane-user"):
    _insert_user(db, user_id=user_id, tier="free", monthly_limit=5.0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET apple_sub = ? WHERE id = ?", (f"anonymous:{uuid.uuid4().hex}", user_id))
    conn.commit(); conn.close()
    svc = JWTService(secret=SECRET, algorithm="HS256", access_expire_minutes=60, refresh_expire_days=30)
    return {"Authorization": f"Bearer {svc.create_access_token(user_id, 'i765')}", **I765}


def _turn(client, headers, text="my first work permit", **over):
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


def test_the_es_family_name_is_a_drill_in_the_agenda_never_lists():
    """The I-765 lead, 2026-10-12, from the client's agenda code: once the
    given name lands in es/pt the line is `q_p2_full_name | p2.middle_name,
    p2.family_name`, and q_p2_family_name is never a line. v1 told the lane to
    decide by "which line was asked" with that second line described, so on
    the family-name turn it saw two name fields and nothing that said a
    double surname is not a middle name."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    assert "the client sends the name as two lines" not in sp
    assert "which the agenda never shows as lines" in sp
    assert '{"node_id": "q_p2_family_name", "field_ids": ["p2.family_name"]}' in sp
    assert "never by which ids the line lists" in sp
    assert '("Rojas Pérez" is one last name), never a middle name' in sp
    assert "Decide by the last question the interviewer asked in THE CONVERSATION SO FAR" in sp


def test_the_es_middle_name_is_checked_once_unless_the_given_name_has_two_words():
    """The lead's ruling, 2026-10-12: the client's es/pt walk is given name,
    family name, middle-name check, middle name. When the given name already
    holds two words the check is never asked and the middle name is minted
    as the stated none, so the line closes and the second word is never
    filed twice."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    i = sp.index("In Spanish and Portuguese the name is asked in up to four questions")
    rule = sp[i:sp.index("Other names (Items 2 to 4)", i)]
    assert rule.index("q_p2_family_name") < rule.index("q_p2_middle_name_check") < rule.index('"q_p2_middle_name"')
    assert '{"node_id": "q_p2_middle_name_check", "field_ids": ["p2.has_middle_name"]}' in rule
    assert "ask ONCE whether she has a legal middle name" in rule
    assert "EXCEPT when p2.given_name already holds two or more words" in rule
    assert 'mint p2.middle_name as the stated none, value ""' in rule
    assert "with her given-name words exactly as she said them as provenance" in rule


def test_the_c9_and_c26_gates_stop_only_on_their_stop_answer():
    """The I-765 lead, 2026-10-12 (DECISIONS 2026-10-09, brief 2.1.1): c9 in
    immigration court and c26 without an approved I-140 are hard stops the
    client's trigger carries; the lane mints the gate, escalation null, says
    the attorney line once and asks the note. An unsure gate never stops."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    i = sp.index("TWO CATEGORIES HAVE A GATE THAT CAN STOP THE INTERVIEW")
    rule = sp[i:i + 2000]
    for node, field in (("q_p2_c9_proceedings", "p2.c9_in_proceedings"),
                        ("q_p2_c26_i140", "p2.c26_i140_approved"),
                        ("q_p2_c9_proceedings_note", "p2.c9_proceedings_note"),
                        ("q_p2_c26_i140_note", "p2.c26_i140_note")):
        assert f"{node} " in rule and field in rule, node
    assert "THE STOP ANSWER IS c9 yes OR c26 no" in rule
    assert "mint the gate value with escalation null" in rule
    assert "AN UNSURE GATE NEVER ENDS THE INTERVIEW" in rule
    assert "mint the category and the gate in that same response" in rule
    # bc85c3e, the lead's correction: a court date can be a hearing about
    # something else, so it is never read as in-proceedings.
    assert "A COURT DATE ALONE IS NOT A YES" in rule
    assert '"c9, and I have a court date" mints c9 only and the gate is asked as written' in rule


def test_the_config_has_the_n400_wire_shape_and_an_i765_brief():
    i765 = json.load(open("config/remote/i765/interviewer-turn.json"))
    n400 = json.load(open("config/remote/n400/interviewer-turn.json"))
    assert i765["requiredVariables"] == n400["requiredVariables"]
    # TODAY (N-400 v62) reached I-765 at v4 (the lead, 2026-10-18), so the two
    # templates are identical again and the client fills one.
    assert i765["optionalVariables"] == n400["optionalVariables"]
    assert "TODAY (her calendar day; empty from an older client):\n{{today}}" in i765["userPromptTemplate"]
    assert i765["userPromptTemplate"] == n400["userPromptTemplate"], "the client fills one template"
    assert i765["maxTokens"] == n400["maxTokens"] and i765["thinking"] == n400["thinking"]
    assert i765["recommendedModel"] == n400["recommendedModel"]
    assert i765["server_only"] is True and i765["version"] == 5
    sp = i765["systemPrompt"]
    assert "Form I-765" in sp and "N-400" not in sp
    # The brief's rules the client depends on (gp-lane-brief-i765.md, 2026-10-09).
    assert "q_p1_reason" in sp and "q_p2_eligibility_category" in sp, "reason first, category second"
    assert "c9, c3a, c3b, c3c, a12, c19, a17, a18, a3, a5, c26, other" in sp, "the catalog's category ids"
    assert "p2.eligibility_category = other" in sp and "escalation null" in sp, "the lead's ruling on other"
    assert "never pick a category" in sp
    assert '"risk_trigger" or "legal_question" or "user_request"' in sp, "the I-765 trigger set"
    assert "legal_advice" not in sp, "the N-400 trigger name never reaches this lane"
    assert '"intent": an OBJECT, never a bare string' in sp, "parity with n400 v54; the I-765 decoder accepts both"
    assert "`target` is present when" in sp and "`disambiguation` is present only" in sp
    assert '"utterance": string, a verbatim span' in sp and '"value_type"' in sp, "the served N-400 fact shape"
    assert '"origin": "applicant" or "capture_gap"' in sp
    assert "[agenda empty]" in sp and "awaiting_confirmation" in sp
    # Phrases only the N-400 rules carry. ("five year" and "oath" appear in
    # this prompt's own sentence saying the form has none of those, so they
    # are not usable as guards.)
    for gone in ("Part 9", "thirteen", "Selective Service", "legal review", "legal_advice", "p9."):
        assert gone not in sp, f"an N-400 rule carried over: {gone}"
    for key in ('"schema_version"', '"intent"', '"facts"', '"asking"', '"section_checkpoint"', '"interview_over"', '"reply"'):
        assert key in sp, key
    assert sp.index('"facts"') < sp.index('"asking"') < sp.index('"reply"'), "decisions before the spoken line, as n400"
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
    assert obj["reply"]["en"].startswith("A first permit")
    assert obj["asking"]["node_id"] == "q_p2_eligibility_category"
    assert obj["intent"] == {"type": "answer"} and obj["facts"][0]["provenance"]["utterance"] == "my first work permit"
    assert obj["section_checkpoint"] == {"part": 1, "section": "Part 1: Why you are applying", "awaiting_confirmation": False}
    assert mock_provider.call_count == 1, "an object needs no retry"
    sent = mock_provider.call_args.args[0] if mock_provider.call_args.args else mock_provider.call_args.kwargs["request"]
    assert "Form I-765" in sent.system_prompt and "N-400" not in sent.system_prompt
    assert sent.max_tokens == 6144
    assert "AGENDA" in sent.user_content and "q_p1_reason" in sent.user_content
    assert "SECTION BOUNDARY" in sent.user_content and "summarize Part 1" in sent.user_content


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
    mock_provider.return_value = _canned("Sure, a first permit. Which eligibility category is on your paperwork?")
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

    body = chat_request(system_prompt="", user_content="my first work permit",
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


def test_today_resolves_her_relative_dates_and_an_anchor_is_never_filed():
    """N-400 v62's TODAY rules, ported to I-765 at v4 (the lead, 2026-10-18),
    without N-400's trip, job and address anchors this form has no field for."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    assert "TODAY IS THE APPLICANT'S OWN CALENDAR DAY, from the TODAY line of the request" in sp
    assert "read the full date back as a question before you mint it" in sp
    assert "When TODAY is empty, never resolve a relative year: ask which year." in sp
    assert "AN ANCHOR MAY BE OFFERED, NEVER FILED" in sp
    assert "you mint it only on their yes, with their yes as its provenance.utterance" in sp
    assert "Wisteria Street" not in sp and "going home" not in sp, "no N-400 anchor leaked in"


def test_a_gate_that_opens_rows_never_ends_its_part():
    """The lead's replay runs, 2026-10-19: on the common path Part 2 ends on
    the SEVIS gate (the interpreter opener answered no files p3.reads_english,
    so Part 3 collapses), and a yes there opens the SEVIS number row. The
    client sends SECTION BOUNDARY on that node, and its presence alone used to
    round the part off. N-400's rule, in I-765 words."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    rule = sp[sp.index("A GATE OR COUNT THAT OPENS ROWS DOES NOT END ITS PART"):]
    rule = rule[:rule.index("A part is not done while")]
    assert "even when SECTION BOUNDARY is present" in rule
    assert "no part summary and no section_checkpoint" in rule
    assert 'Only "no", "none" or zero closes the part there' in rule
    assert "What is your SEVIS number?" in rule


def test_the_judge_partials_the_prompt_caused_are_closed():
    """The lead's blind judge, 2026-10-19 (no paid calls): a TODAY read-back
    and a last-arrival ask that kept talking after the question mark, both
    taught by the prompt's own examples, and an opening question filed under
    an invented node id where N-400's rule says null."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    assert '("March of last year, so March 2025, and what day, if you have it?")' in sp
    assert '("and what day, if you have it, or where could you check?")' in sp
    assert "And the day, if you have it.\")" not in sp and "if not, where you could check" not in sp
    assert "null when your question is not for any agenda node (a BEFORE WE BEGIN question" in sp
    assert "never invent a node id" in sp


def test_a_verbatim_opening_question_does_not_count_against_the_budget():
    """The lead, 2026-10-19: the interpreter opener stays in the form's own
    words (Scott, 2026-09-30) and is 28 words alone, so the 45 word budget
    could only be met by cutting the question it is not allowed to cut. The
    judge's 53 word opening also led with "Hi"."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    assert "is not counted against the 45 and is never shortened to fit" in sp
    assert "the three things before it then take 25 words or fewer, ALL THREE of them" in sp
    assert "dropping one of the three to fit is the defect" in sp
    assert 'no greeting ("Hi", "Hello")' in sp


def test_the_re_judge_partials_are_closed():
    """The lead's re-judge on the #1133 head (2026-10-19, no paid calls): a
    SEVIS gloss spoken after the question mark, and asking null after the
    SSN gate's yes but named after the SEVIS gate's yes. One answer: the row."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    assert "the same holds for an explanation you add yourself" in sp
    assert "inside the question, or not said at all" in sp
    assert 'a yes to the Social Security gate is {"node_id": "q_p2_ssn", "field_ids": ["p2.ssn"]}' in sp
    assert 'a yes to the SEVIS gate is {"node_id": "q_p2_sevis_number", "field_ids": ["p2.sevis_number"]}' in sp
    assert "asking names that row, never null" in sp


def test_the_lane_never_states_a_fee_payee_or_address():
    """Scott, 2026-10-19 ("yes strip it", relayed by the I-765 lead): the app
    never says where to mail, whom to pay or how much; it points to the
    official I-765 page and the instructions, like the N-400. Before v5 the
    lane told her the app's summary screen shows the fee and the address,
    which is false once the client drops those blocks."""
    sp = json.load(open("config/remote/i765/interviewer-turn.json"))["systemPrompt"]
    assert "YOU NEVER STATE DATES, FEES, PAYEES OR ADDRESSES" in sp
    assert "the official Form I-765 page on uscis.gov and the form's instructions" in sp
    assert "never a payee" in sp
    assert "What to gather screen" in sp
    assert '"What to gather" in English, "Qué reunir" in Spanish, "O que reunir" in Portuguese' in sp, "her words match the app's labels"
    assert "summary screen" not in sp, "the old promise that the app shows the fee"
