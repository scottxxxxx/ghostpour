"""`report_language` on POST /v1/meetings/{id}/report (SS, 2026-09-10).

SS's translation redesign regenerates a meeting's report per language from
the original transcript, sending `report_language: <target>` beside
`transcript_language`. Before this the generate body had no such field, so
pydantic dropped the key, the report's language resolved from the
transcript, and the echoed `report_language` was GP's RESOLVED value: a
client comparing echo to request saw a mismatch on every cross-language
regeneration. The render route already honoured it, which is why it looked
wired.

Precedence: report_language, then transcript_language, then Accept-Language.
These are request-side tests at the route: the value on the wire in must be
the value in the prompt and in the echo out.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.models.chat import ChatResponse

MODEL_JSON = {
    "header": {"category": "Working Session", "title": "t", "summary": "s", "attendees": []},
    "stoplight": {"color": "green", "label": "ok", "detail": "d"},
    "sentiment": {"score": 55, "label": "fine", "detail": "d", "category": "informational",
                  "category_evidence": "", "arc": [], "arc_narrative": "n"},
    "suggested_tags": [], "actions": [], "decisions": [], "technical_issues": [],
    "open_questions": [], "queries_during_meeting": [],
}


def _seed(db_path, user_id):
    meeting_id = "lang-" + uuid.uuid4().hex[:8]
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO meeting_transcripts (id, user_id, meeting_id, transcript, project, project_id, created_at)
           VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
        (str(uuid.uuid4()), user_id, meeting_id, "[Antonio] Y todos salimos.\n[Marc] Empecemos.",
         datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    return meeting_id


def _wire(monkeypatch):
    calls = []

    async def fake_route(chat_request):
        calls.append(chat_request)
        return ChatResponse(text=json.dumps(MODEL_JSON), input_tokens=10, output_tokens=20,
                            model="claude-sonnet-4-6", provider="anthropic",
                            usage={"input_tokens": 10, "output_tokens": 20})

    from app.main import app as _app
    monkeypatch.setattr(_app.state.provider_router, "route", AsyncMock(side_effect=fake_route))
    return calls


def _post(client, user, meeting_id, *, accept_language=None, **body):
    h = {**user["headers"], "X-App-ID": "shouldersurf", "X-App-Build": "2000"}
    if accept_language:
        h["Accept-Language"] = accept_language
    return client.post(f"/v1/meetings/{meeting_id}/report",
                       json={"duration_seconds": 600, **body}, headers=h)


def _report_prompt(calls) -> str:
    # The report call is the one whose user message carries the transcript;
    # cleanup sub-calls, when they run, come first.
    return calls[-1].system_prompt


def test_the_requested_language_wins_over_the_stated_one(client, pro_user, tmp_db_path, monkeypatch):
    """A Spanish meeting regenerated in French from a Spanish phone."""
    calls = _wire(monkeypatch)
    mid = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, mid, accept_language="es-MX",
              transcript_language="es-US", report_language="fr")
    assert r.status_code == 200, r.text
    assert r.json()["report_language"] == "fr"
    assert "'fr'" in _report_prompt(calls)


def test_an_english_phone_regenerating_english_gets_an_english_directive(client, pro_user, tmp_db_path, monkeypatch):
    """The case that motivated the ruling: without a directive the served
    recipe wrote in the transcript's language."""
    calls = _wire(monkeypatch)
    mid = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, mid, accept_language="en-US",
              transcript_language="es-US", report_language="en")
    assert r.status_code == 200, r.text
    assert r.json()["report_language"] == "en"
    assert "LANGUAGE:" in _report_prompt(calls)
    assert "'en'" in _report_prompt(calls)


def test_without_report_language_the_stated_language_still_wins(client, pro_user, tmp_db_path, monkeypatch):
    """Older builds send transcript_language only. Unchanged behaviour."""
    calls = _wire(monkeypatch)
    mid = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, mid, accept_language="en-US", transcript_language="es-US")
    assert r.status_code == 200, r.text
    assert r.json()["report_language"] == "es"
    assert "'es'" in _report_prompt(calls)


def test_a_malformed_report_language_falls_through_to_the_stated_one(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    mid = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, mid, accept_language="en-US",
              transcript_language="es-US", report_language="français!")
    assert r.status_code == 200, r.text
    assert r.json()["report_language"] == "es"
