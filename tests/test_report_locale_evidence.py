"""Every report row in usage_log says which locale its language directive
was built from and what the client stated.

2026-08-25: a Spanish meeting's stored report was English. The 08-21
generation (build 1131) had carried LANGUAGE 'es'; a 08-24 regeneration
(build 335) stated no transcript_language and sent an English
Accept-Language, so no directive was appended. Proving that took the
provider raw_request plus the edge access log, because nothing GP stored
recorded the resolved locale or the client's statement. Now the row does.
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
    meeting_id = "locale-evidence-" + uuid.uuid4().hex[:8]
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO meeting_transcripts
           (id, user_id, meeting_id, transcript, project, project_id, created_at)
           VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
        (str(uuid.uuid4()), user_id, meeting_id,
         "[Antonio] Y todos salimos.\n[Marc] Empecemos.",
         datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    return meeting_id


def _report_row(db_path, user_id):
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT metadata FROM usage_log WHERE user_id = ? AND call_type = 'report' "
        "ORDER BY request_timestamp DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return json.loads(row["metadata"]) if row and row["metadata"] else {}


def _wire(client, pro_user, tmp_db_path, monkeypatch):
    seen = {}

    async def fake_route(chat_request):
        seen["system"] = chat_request.system_prompt
        return ChatResponse(text=json.dumps(MODEL_JSON), input_tokens=10, output_tokens=20,
                            model="claude-sonnet-4-6", provider="anthropic",
                            usage={"input_tokens": 10, "output_tokens": 20})

    from app.main import app as _app
    monkeypatch.setattr(_app.state.provider_router, "route", AsyncMock(side_effect=fake_route))
    return seen


def test_a_stated_language_is_recorded_beside_the_locale_it_resolved_to(client, pro_user, tmp_db_path, monkeypatch):
    seen = _wire(client, pro_user, tmp_db_path, monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = client.post(f"/v1/meetings/{meeting_id}/report",
                    json={"duration_seconds": 600, "transcript_language": "es-US"},
                    headers={**pro_user["headers"], "X-App-ID": "shouldersurf", "Accept-Language": "en-US"})
    assert r.status_code == 200, r.text
    # the directive the model saw and the row agree (check the echo)
    assert "BCP-47 code 'es'" in seen["system"]
    assert _report_row(tmp_db_path, pro_user["user_id"])["report"] == {
        "locale": "es", "transcript_language": "es-US"}


def test_the_08_24_shape_is_visible_as_nothing_stated_and_no_locale(client, pro_user, tmp_db_path, monkeypatch):
    seen = _wire(client, pro_user, tmp_db_path, monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = client.post(f"/v1/meetings/{meeting_id}/report",
                    json={"duration_seconds": 600},
                    headers={**pro_user["headers"], "X-App-ID": "shouldersurf", "Accept-Language": "en-US"})
    assert r.status_code == 200, r.text
    assert "LANGUAGE:" not in seen["system"]
    # both keys PRESENT with null values: absence of the block would read
    # as "not recorded", which is the ambiguity this exists to remove
    assert _report_row(tmp_db_path, pro_user["user_id"])["report"] == {
        "locale": None, "transcript_language": None}
