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


def _insert_row(db_path, user_id, scenario, app_id="techrehearsal"):
    import uuid
    from datetime import datetime, timezone
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO usage_log (id, user_id, provider, model, input_tokens, "
        "output_tokens, estimated_cost_usd, request_timestamp, response_time_ms, "
        "status, call_type, app_id, scenario) "
        "VALUES (?, ?, 'anthropic', 'm', 100, 50, 0.01, ?, 100, 'success', 'chat', ?, ?)",
        (str(uuid.uuid4()), user_id, datetime.now(timezone.utc).isoformat(), app_id, scenario),
    )
    conn.commit()
    conn.close()


def test_dashboard_by_scenario_breakdown(client, tmp_db_path):
    from tests.conftest import _insert_user

    _insert_user(tmp_db_path, user_id="sc-user", tier="pro", monthly_limit=5.10)
    for _ in range(2):
        _insert_row(tmp_db_path, "sc-user", "interview")
    _insert_row(tmp_db_path, "sc-user", "negotiation")
    _insert_row(tmp_db_path, "sc-user", "personal")
    _insert_row(tmp_db_path, "sc-user", None)                       # untagged
    _insert_row(tmp_db_path, "sc-user", "interview", app_id="shouldersurf")  # other app

    admin = {"X-Admin-Key": "test-admin-key"}
    allapps = client.get("/webhooks/admin/dashboard?days=7", headers=admin).json()
    by = {s["scenario"]: s["requests"] for s in allapps["by_scenario"]}
    assert by.get("interview") == 3       # 2 TR + 1 SS
    assert by.get("negotiation") == 1
    assert by.get("personal") == 1
    assert by.get("(untagged)") == 1

    # Honors the app filter: scoped to techrehearsal, the SS interview drops out.
    tr = client.get("/webhooks/admin/dashboard?days=7&app=techrehearsal", headers=admin).json()
    by_tr = {s["scenario"]: s["requests"] for s in tr["by_scenario"]}
    assert by_tr.get("interview") == 2
