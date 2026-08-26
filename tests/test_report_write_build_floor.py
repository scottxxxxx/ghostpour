"""Report WRITES have a build floor; every report response says its language.

Scott via CQ, 2026-08-26. Shoulder Surf build 335 (predates the
transcript_language field) regenerated a Spanish meeting's report in
English on 08-24 and iCloud sync overwrote the Spanish one. Three fixes,
one per side; GP's is (c): a build below `report_write_min_build` (1193, the first build that states transcript_language) (served
per app in app-versions.yml) gets 412 report_build_floor on POST
/v1/meetings/{id}/report and nothing else changes for it. Plus the echo
SS needs for (a): `report_language` (generated language, "en" when no
directive) and `transcript_language` (stated, raw) on generate, fetch and
render.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.models.chat import ChatResponse
from app.services import version_gate

MODEL_JSON = {
    "header": {"category": "Working Session", "title": "t", "summary": "s", "attendees": []},
    "stoplight": {"color": "green", "label": "ok", "detail": "d"},
    "sentiment": {"score": 55, "label": "fine", "detail": "d", "category": "informational",
                  "category_evidence": "", "arc": [], "arc_narrative": "n"},
    "suggested_tags": [], "actions": [], "decisions": [], "technical_issues": [],
    "open_questions": [], "queries_during_meeting": [],
}
UA_335 = "Shoulder%20Surf/335 CFNetwork/3860.700.1 Darwin/25.6.0"


def _seed(db_path, user_id):
    meeting_id = "floor-" + uuid.uuid4().hex[:8]
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


def _floor(client):
    from app.main import app as _app
    from app.routers.config import load_apps
    return version_gate.report_write_floor(_app.state.app_versions, load_apps(), "shouldersurf")


def _post(client, user, meeting_id, *, build=None, ua=None, app="shouldersurf", **body):
    h = {**user["headers"], "X-App-ID": app}
    if build is not None:
        h["X-App-Build"] = str(build)
    if ua is not None:
        h["User-Agent"] = ua
    return client.post(f"/v1/meetings/{meeting_id}/report", json={"duration_seconds": 600, **body}, headers=h)


def _rows(db_path, meeting_id):
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM meeting_reports WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]
    conn.close()
    return n


# --- the floor ------------------------------------------------------------------

def test_the_served_floor_is_the_first_build_that_states_transcript_language(client):
    assert _floor(client) == 1193


def test_a_build_below_the_floor_is_refused_by_name_and_writes_nothing(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, build=335)
    assert r.status_code == 412, r.text
    assert r.json()["detail"] == {
        "code": "report_build_floor",
        "message": "This build cannot generate reports; update Shoulder Surf.",
        "min_build": 1193, "app_build": 335, "recovery_action": "update_app"}
    assert calls == [], "a refused write must never reach the model"
    assert _rows(tmp_db_path, meeting_id) == 0


def test_build_335_is_read_off_the_user_agent_when_no_build_header_rides(client, pro_user, tmp_db_path, monkeypatch):
    # the 08-24 regeneration's actual request shape (edge log): UA only
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, ua=UA_335)
    assert r.status_code == 412 and r.json()["detail"]["app_build"] == 335
    assert calls == []


def test_a_build_at_the_floor_still_writes(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, build=1193, transcript_language="es-US")
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and _rows(tmp_db_path, meeting_id) == 1


def test_no_readable_build_is_allowed_never_guessed(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, ua="python-httpx/0.27")
    assert r.status_code == 200, r.text
    assert len(calls) == 1


def test_an_app_without_a_floor_is_untouched(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, build=1, app="techrehearsal")
    assert r.status_code == 200, r.text
    assert len(calls) == 1


def test_reads_are_not_gated(client, pro_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    assert _post(client, pro_user, meeting_id, build=1193).status_code == 200
    r = client.get(f"/v1/meetings/{meeting_id}/report",
                   headers={**pro_user["headers"], "X-App-ID": "shouldersurf", "X-App-Build": "335"})
    assert r.status_code == 200


def test_build_parsing_prefers_the_header_and_reads_only_this_apps_user_agent():
    SS = "Shoulder Surf"
    assert version_gate.build_number("1262", UA_335, SS) == 1262
    assert version_gate.build_number(None, UA_335, SS) == 335
    assert version_gate.build_number(None, "Shoulder Surf/1200 CFNetwork/1", SS) == 1200
    # another app's token, a browser, a test client: unknown, never "build 0"
    assert version_gate.build_number(None, "Tech%20Rehearsal/900 CFNetwork/1", SS) is None
    assert version_gate.build_number(None, "Mozilla/5.0 (Macintosh)", SS) is None
    assert version_gate.build_number(None, "python-httpx/0.27", SS) is None
    assert version_gate.build_number(None, UA_335, None) is None  # no name served: no UA reading
    assert version_gate.build_number("1.2", None, SS) is None
    assert version_gate.build_number("", "python-httpx/0.27", SS) is None
    assert version_gate.report_write_floor({}, {"apps": {}}, "shouldersurf") is None
    reg = {"com.x.y": {"platforms": {"ios": {"report_write_min_build": "0"}}}}
    assert version_gate.report_write_floor(reg, {"apps": {"x": {"bundle_id": "com.x.y"}}}, "x") is None


# --- the echo -------------------------------------------------------------------

def test_generate_and_fetch_say_the_language_the_report_was_written_in(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, build=1200, transcript_language="es-US")
    assert r.status_code == 200, r.text
    assert "BCP-47 code 'es'" in calls[0].system_prompt  # the directive the model saw
    assert r.json()["report_language"] == "es" and r.json()["transcript_language"] == "es-US"
    g = client.get(f"/v1/meetings/{meeting_id}/report", headers={**pro_user["headers"], "X-App-ID": "shouldersurf"})
    assert g.status_code == 200
    assert g.json()["cached"] is True
    assert g.json()["report_language"] == "es" and g.json()["transcript_language"] == "es-US"


def test_no_directive_means_english_not_null(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, build=1200)
    assert r.status_code == 200, r.text
    assert "LANGUAGE:" not in calls[0].system_prompt
    assert r.json()["report_language"] == "en" and r.json()["transcript_language"] is None


def test_a_report_cached_before_the_tag_existed_reads_as_untagged(client, pro_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    assert _post(client, pro_user, meeting_id, build=1200, transcript_language="es").status_code == 200
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE meeting_reports SET report_language = NULL, transcript_language = NULL WHERE meeting_id = ?", (meeting_id,))
    conn.commit(); conn.close()
    g = client.get(f"/v1/meetings/{meeting_id}/report", headers={**pro_user["headers"], "X-App-ID": "shouldersurf"})
    assert g.json()["report_language"] is None and g.json()["transcript_language"] is None


def test_render_echoes_the_language_the_client_stored_and_never_invents_one(client, pro_user):
    body = {"report_json": MODEL_JSON, "duration_seconds": 600, "report_language": "es"}
    r = client.post("/v1/reports/render", json=body, headers={**pro_user["headers"], "X-App-ID": "shouldersurf"})
    assert r.status_code == 200, r.text
    assert r.json()["report_language"] == "es" and "report_html" in r.json()
    r2 = client.post("/v1/reports/render", json={"report_json": MODEL_JSON, "duration_seconds": 600},
                     headers={**pro_user["headers"], "X-App-ID": "shouldersurf"})
    assert r2.status_code == 200 and r2.json()["report_language"] is None
