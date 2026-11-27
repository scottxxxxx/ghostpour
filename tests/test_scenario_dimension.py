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


# --- Request labels (2026-11-26): round_format, phase, grounding, practice_focus.
# Request side first (CLAUDE.md rule 3): before these columns an unknown
# metadata key was accepted and silently dropped, so a client could send a
# label correctly and no row would ever hold it.

def _latest(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM usage_log WHERE user_id = ? ORDER BY request_timestamp DESC LIMIT 1",
        (user_id,)).fetchone()
    conn.close()
    return dict(row)


def test_each_request_label_sent_reaches_its_own_column(client, free_user, mock_provider,
                                                        tmp_db_path):
    # Four different values, so a label stored in the wrong column shows.
    resp = client.post("/v1/chat", headers=free_user["headers"], json=chat_request(
        call_type="tr_response_analysis",
        metadata={"scenario": "interview", "scenario_kind": "jobInterview",
                  "round_format": "Behavioral, system_design", "phase": " Practice ",
                  "grounding": "jd_resume", "practice_focus": "weak_answer"}))
    assert resp.status_code == 200
    row = _latest(tmp_db_path, free_user["user_id"])
    assert (row["round_format"], row["phase"], row["grounding"], row["practice_focus"]) == (
        "behavioral,system_design", "practice", "jd_resume", "weak_answer")
    assert (row["scenario"], row["scenario_kind"]) == ("interview", "jobInterview")


def test_absent_labels_are_null_and_text_that_is_not_a_label_is_not_stored(
        client, free_user, mock_provider, tmp_db_path):
    assert client.post("/v1/chat", json=chat_request(), headers=free_user["headers"]).status_code == 200
    row = _latest(tmp_db_path, free_user["user_id"])
    assert [row[k] for k in ("round_format", "phase", "grounding", "practice_focus")] == [None] * 4
    resp = client.post("/v1/chat", headers=free_user["headers"], json=chat_request(metadata={
        "phase": "I am preparing for my interview at Acme", "grounding": 7,
        "practice_focus": "x" * 25, "round_format": "behavioral;drop table"}))
    assert resp.status_code == 200        # never rejected, only not stored
    row = _latest(tmp_db_path, free_user["user_id"])
    assert [row[k] for k in ("round_format", "phase", "grounding", "practice_focus")] == [None] * 4


def test_the_round_list_of_all_ten_fits():
    from app.services.usage_tracker import REQUEST_LABELS, _normalize_label
    ten = ("recruiter_screen,hiring_manager,behavioral,technical,system_design,"
           "panel,demo,bar_raiser,culture_fit,case")
    assert _normalize_label(ten, REQUEST_LABELS["round_format"]) == ten


def _insert_typed(db_path, user_id, **cols):
    import uuid
    from datetime import datetime, timezone
    base = {"id": str(uuid.uuid4()), "user_id": user_id, "provider": "anthropic", "model": "m",
            "input_tokens": 100, "output_tokens": 50, "estimated_cost_usd": 0.01,
            "request_timestamp": datetime.now(timezone.utc).isoformat(),
            "response_time_ms": 100, "status": "success", "app_id": "techrehearsal", **cols}
    conn = sqlite3.connect(db_path)
    conn.execute(f"INSERT INTO usage_log ({', '.join(base)}) VALUES ({', '.join('?' for _ in base)})",
                 tuple(base.values()))
    conn.commit()
    conn.close()


def test_the_dashboard_lists_requests_by_type(client, tmp_db_path):
    from tests.conftest import _insert_user
    _insert_user(tmp_db_path, user_id="rt", tier="pro", monthly_limit=5.10)
    for _ in range(2):
        _insert_typed(tmp_db_path, "rt", scenario="interview", scenario_kind="jobInterview",
                      call_type="tr_response_analysis", prompt_mode="InterviewFollowUp",
                      phase="practice", round_format="behavioral", grounding="jd_resume")
    _insert_typed(tmp_db_path, "rt", scenario="other", scenario_kind="hardConversation",
                  call_type="tr_counterpart_turn", phase="practice")
    _insert_typed(tmp_db_path, "rt", call_type="tr_summary", prompt_mode="AutoSummary")
    _insert_typed(tmp_db_path, "rt", scenario="unattached", call_type="tr_query")
    # A Shoulder Surf call with no scenario is not scenario-driven traffic.
    _insert_typed(tmp_db_path, "rt", app_id="shouldersurf", call_type="query")

    admin = {"X-Admin-Key": "test-admin-key"}
    rows = client.get("/webhooks/admin/request-types?days=7", headers=admin).json()["by_request_type"]
    assert {r["app_id"] for r in rows} == {"techrehearsal"}
    top = rows[0]
    assert (top["label"], top["call_type"], top["prompt_mode"], top["phase"], top["round_format"],
            top["grounding"], top["practice_focus"], top["requests"]) == (
        "Job interview", "tr_response_analysis", "InterviewFollowUp", "practice", "behavioral",
        "jd_resume", None, 2)
    by_step = {r["call_type"]: r["label"] for r in rows}
    assert by_step == {"tr_response_analysis": "Job interview",
                       "tr_counterpart_turn": "Something else",
                       "tr_summary": "(untagged)", "tr_query": "No rehearsal attached"}
    only_ss = client.get("/webhooks/admin/request-types?days=7&app=shouldersurf", headers=admin).json()
    assert only_ss["by_request_type"] == []
    assert client.get("/webhooks/admin/request-types?days=7").status_code in (401, 403, 422)


def test_jev_shows_in_the_model_list_from_its_own_ledger(client, tmp_db_path):
    import uuid
    from datetime import datetime, timezone
    admin = {"X-Admin-Key": "test-admin-key"}
    assert not [m for m in client.get("/webhooks/admin/dashboard?days=7", headers=admin).json()["by_model"]
                if m["model"] == "jev"]
    conn = sqlite3.connect(tmp_db_path)
    for app_id, cost in (("n400", 0.0002), ("n400", 0.0004), ("shouldersurf", 0.0001)):
        conn.execute(
            "INSERT INTO typesafe_calls (id, created_at, app_id, judgment, mode, outcome, "
            "fell_back, jev_ms, input_tokens, cost_usd) VALUES (?, ?, ?, 'j', 'primary', 'ok', 0, 300, 120, ?)",
            (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), app_id, cost))
    conn.commit()
    conn.close()
    jev = [m for m in client.get("/webhooks/admin/dashboard?days=7", headers=admin).json()["by_model"]
           if m["model"] == "jev"]
    assert len(jev) == 1
    assert (jev[0]["provider"], jev[0]["requests"], jev[0]["input_tokens"], jev[0]["cost_usd"],
            jev[0]["avg_latency_ms"], jev[0]["separate_ledger"]) == ("typesafe", 3, 360, 0.0007, 300, True)
    n400 = [m for m in client.get("/webhooks/admin/dashboard?days=7&app=n400", headers=admin).json()["by_model"]
            if m["model"] == "jev"]
    assert (n400[0]["requests"], n400[0]["cost_usd"]) == (2, 0.0006)
