"""Queries answer in the language the user typed (Scott, 2026-11-25).

"the query should come back in the langauge that the user typed the query
in" and "this hsould apply to all queries". The receipt: on 2026-11-23 a
Spanish phone in an English class tapped Help Me Respond and got Spanish,
because the phone-language directive overrode TRANSCRIPT LANGUAGE: en-US.

Non-query calls (summary, report, title, analysis, translation) keep the
phone's language exactly as before.
"""
import pytest

from app.services.locale_injection import QUERY_CALL_TYPES, language_directive
from tests.conftest import chat_request

TYPED = "Answer in the language of the question or message the user typed."


@pytest.mark.parametrize("ct", sorted(QUERY_CALL_TYPES))
def test_every_query_type_answers_in_the_typed_language(ct):
    d = language_directive("es", ct)
    assert TYPED in d
    assert "Respond in Spanish (es). This overrides" not in d
    assert "otherwise respond in Spanish (es)" in d
    assert "language that conversation is being held in" in d


@pytest.mark.parametrize("ct", ["summary", "report", "meeting_title", "analysis",
                                "translation", None])
def test_non_query_calls_keep_the_phone_language(ct):
    d = language_directive("es", ct)
    assert d.startswith("\n\n--- RESPONSE LANGUAGE ---\nRespond in Spanish (es). This overrides")
    assert TYPED not in d


def test_query_directive_has_no_dash_punctuation():
    d = language_directive("es", "query")
    assert " - " not in d and "—" not in d and "–" not in d


def _system_sent(mock_provider):
    req = mock_provider.call_args.args[0] if mock_provider.call_args.args else \
        next(iter(mock_provider.call_args.kwargs.values()))
    return req.system_prompt


@pytest.mark.parametrize("ct,typed", [("query", True), ("summary", False)])
def test_the_route_sends_the_rule_the_call_type_earns(client, free_user, mock_provider, ct, typed):
    r = client.post("/v1/chat", json=chat_request(call_type=ct, locale="es"),
                    headers={**free_user["headers"], "X-App-ID": "shouldersurf"})
    assert r.status_code == 200, r.text
    assert (TYPED in _system_sent(mock_provider)) is typed


@pytest.mark.parametrize("ct,echo", [("query", None), ("meeting_chat", None), ("summary", "es")])
def test_output_language_is_null_for_queries_and_kept_elsewhere(client, free_user, mock_provider, ct, echo):
    r = client.post("/v1/chat", json=chat_request(call_type=ct, locale="es"),
                    headers={**free_user["headers"], "X-App-ID": "shouldersurf"})
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == echo
    assert r.headers.get("X-Output-Locale") == echo
