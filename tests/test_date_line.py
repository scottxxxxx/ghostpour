"""Today's date on SS conversational calls (2026-10-29 prod review).

None of the 210 distinct SS system prompts in 30 days of real calls carried a
date. Checked on the request GP forwards, not on the response, because the
hole is request-side (CLAUDE.md rule 3).
"""
from datetime import datetime, timezone

from app.services.date_line import apply, date_line
from tests.conftest import chat_request

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)


def _headers(user, app="shouldersurf"):
    return {**user["headers"], "X-App-ID": app}


def _forwarded_system(mock_provider) -> str:
    return mock_provider.call_args.args[0].system_prompt


def test_the_line_names_the_utc_date_and_says_it_is_utc():
    line = date_line(NOW)
    assert "Today is Thursday 2026-10-29 (UTC;" in line


def test_a_named_timezone_moves_the_date():
    assert "Today is Wednesday 2026-10-28 (America/Los_Angeles)" in date_line(
        NOW, "America/Los_Angeles")


def test_an_unknown_timezone_falls_back_to_utc():
    assert "Thursday 2026-10-29 (UTC;" in date_line(NOW, "Mars/Olympus")


def test_only_ss_conversational_calls_get_it():
    assert apply("p", "shouldersurf", "query", NOW) != "p"
    assert apply("p", "shouldersurf", "query_follow_up", NOW) != "p"
    assert apply("p", "shouldersurf", "summary", NOW) == "p"
    assert apply("p", "techrehearsal", "query", NOW) == "p"


def test_a_copilot_tap_forwards_the_date_before_the_language_directive(
        client, free_user, mock_provider):
    r = client.post("/v1/chat",
                    json=chat_request(call_type="query", prompt_mode="Help Me Respond",
                                      locale="es"),
                    headers=_headers(free_user))
    assert r.status_code == 200, r.text
    system = _forwarded_system(mock_provider)
    assert "--- TODAY ---" in system
    assert system.index("--- TODAY ---") < system.index("--- RESPONSE LANGUAGE ---")
    assert system.rstrip().endswith(
        "Do not translate, rename, add, remove, or reorder keys."), (
        "the language directive must stay last")


def test_a_summary_call_forwards_no_date(client, free_user, mock_provider):
    r = client.post("/v1/chat", json=chat_request(call_type="summary", locale="en"),
                    headers=_headers(free_user))
    assert r.status_code == 200, r.text
    assert "--- TODAY ---" not in _forwarded_system(mock_provider)
