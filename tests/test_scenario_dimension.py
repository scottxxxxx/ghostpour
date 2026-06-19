"""Per-call scenario sub-dimension on usage_log.

Tech Rehearsal is scenario-driven (interview / negotiation / personal) under
one app_id. The client tags each call via metadata.scenario; GP persists it
so analytics can slice scenarios without splitting app_id. NULL when absent.
"""

import sqlite3

from app.services.usage_tracker import _normalize_scenario
from tests.conftest import chat_request


def test_normalize_scenario():
    assert _normalize_scenario("Interview ") == "interview"   # trim + lowercase
    assert _normalize_scenario("PERSONAL") == "personal"
    assert _normalize_scenario("") is None
    assert _normalize_scenario(None) is None
    assert _normalize_scenario(123) is None
    assert _normalize_scenario("x" * 100) == "x" * 32          # length-capped


def _latest_scenario(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT scenario FROM usage_log WHERE user_id = ? "
        "ORDER BY request_timestamp DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["scenario"] if row else "NO_ROW"


def test_scenario_persisted_from_metadata(client, free_user, mock_provider, tmp_db_path):
    resp = client.post(
        "/v1/chat",
        json=chat_request(metadata={"scenario": "Interview "}, call_type="tr_intake"),
        headers=free_user["headers"],
    )
    assert resp.status_code == 200
    assert _latest_scenario(tmp_db_path, free_user["user_id"]) == "interview"


def test_scenario_null_when_absent(client, free_user, mock_provider, tmp_db_path):
    resp = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
    assert resp.status_code == 200
    assert _latest_scenario(tmp_db_path, free_user["user_id"]) is None
