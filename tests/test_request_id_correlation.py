"""X-Request-ID correlation on usage_log.

Partner harnesses quote the X-Request-ID response header verbatim when
reporting runs. The logging middleware mints it; the chat handler stamps it
into the request meta bag; log_usage lands it in usage_log metadata — so a
partner-quoted id becomes a one-line query.
"""

import json
import sqlite3

from tests.conftest import chat_request


def _latest_metadata(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT metadata FROM usage_log WHERE user_id = ? "
        "ORDER BY request_timestamp DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return json.loads(row["metadata"]) if row and row["metadata"] else {}


def test_response_header_matches_usage_row(client, free_user, mock_provider, tmp_db_path):
    resp = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid, "middleware should mint X-Request-ID on every response"
    assert _latest_metadata(tmp_db_path, free_user["user_id"]).get("request_id") == rid


def test_client_sent_request_id_is_overwritten(client, free_user, mock_provider, tmp_db_path):
    resp = client.post(
        "/v1/chat",
        json=chat_request(metadata={"request_id": "spoofed-by-client"}),
        headers=free_user["headers"],
    )
    assert resp.status_code == 200
    stored = _latest_metadata(tmp_db_path, free_user["user_id"]).get("request_id")
    assert stored == resp.headers["X-Request-ID"]
    assert stored != "spoofed-by-client"
