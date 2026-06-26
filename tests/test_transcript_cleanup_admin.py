"""Admin transcript-cleanup tools: original-vs-cleaned view + on-demand clean.

GET  /webhooks/admin/meeting/{id}/transcripts   -> raw + cleaned
POST /webhooks/admin/meeting/{id}/clean-transcript -> run cleanup, persist, return both
"""

import sqlite3
import uuid
from datetime import datetime, timezone

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _insert_transcript(db_path, meeting_id, transcript, user_id="test-pro-user"):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO meeting_transcripts (id, user_id, meeting_id, transcript, created_at) VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, meeting_id, transcript, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def test_get_meeting_transcripts(client, pro_user, tmp_db_path):
    _insert_transcript(tmp_db_path, "MTG1", "raw ocr text", user_id=pro_user["user_id"])
    d = client.get("/webhooks/admin/meeting/MTG1/transcripts", headers=ADMIN).json()
    assert d["raw"] == "raw ocr text"
    assert d["cleaned"] is None
    assert d["raw_chars"] == len("raw ocr text") and d["cleaned_chars"] == 0
    # unknown meeting -> 404
    assert client.get("/webhooks/admin/meeting/NOPE/transcripts", headers=ADMIN).status_code == 404


def test_clean_meeting_transcript_persists_and_returns_both(client, pro_user, tmp_db_path, monkeypatch):
    _insert_transcript(tmp_db_path, "MTG2", "raw raw dupes dupes contamination", user_id=pro_user["user_id"])

    async def _fake_clean(*a, **k):
        return "cleaned tidy text"
    # endpoint does a local `from app.services.transcript_cleanup import clean_transcript`
    monkeypatch.setattr("app.services.transcript_cleanup.clean_transcript", _fake_clean)

    r = client.post("/webhooks/admin/meeting/MTG2/clean-transcript", headers=ADMIN)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cleaned"] == "cleaned tidy text"
    assert d["cleaned_chars"] == len("cleaned tidy text") and d["cleaned_at"]

    # persisted: a subsequent GET returns the cleaned version
    g = client.get("/webhooks/admin/meeting/MTG2/transcripts", headers=ADMIN).json()
    assert g["cleaned"] == "cleaned tidy text" and g["cleaned_at"]


def test_clean_meeting_transcript_errors(client, pro_user, tmp_db_path, monkeypatch):
    _insert_transcript(tmp_db_path, "MTG3", "raw text", user_id=pro_user["user_id"])

    async def _none(*a, **k):
        return None
    monkeypatch.setattr("app.services.transcript_cleanup.clean_transcript", _none)
    # cleanup produced nothing -> 502
    assert client.post("/webhooks/admin/meeting/MTG3/clean-transcript", headers=ADMIN).status_code == 502
    # unknown meeting -> 404
    assert client.post("/webhooks/admin/meeting/NOPE/clean-transcript", headers=ADMIN).status_code == 404


def test_admin_key_required(client):
    assert client.get("/webhooks/admin/meeting/X/transcripts", headers={"X-Admin-Key": "wrong"}).status_code == 403
    assert client.post("/webhooks/admin/meeting/X/clean-transcript", headers={"X-Admin-Key": "wrong"}).status_code == 403
