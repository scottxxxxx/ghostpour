"""A degraded recall is an INCIDENT, not a log line (2026-08-24: every
Free people-scoped recall had 500'd silently for eleven days)."""
import sqlite3
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import context_quilt as cq

SYS = "You are a meeting assistant.\n\n{{context_quilt}}"


def _chat(client, user, meta):
    return client.post("/v1/chat", json={
        "provider": "anthropic", "model": "auto", "system_prompt": SYS,
        "user_content": "What did we decide?", "context_quilt": True,
        "metadata": {"call_type": "query", "prompt_mode": "ProjectChat", **meta}},
        headers=user["headers"])


def _incidents(db_path):
    return sqlite3.connect(db_path).execute(
        "SELECT subject, resolved_at FROM alert_incidents WHERE category='cq_recall_degraded' ORDER BY id"
    ).fetchall()


@pytest.mark.anyio
async def test_recall_degrade_result_names_reason_and_scope(app_env):
    """The service-level contract the route relies on: a 5xx from CQ
    comes back as a result carrying `degraded` and `recall_scope`."""
    from app.config import get_settings
    if not get_settings().cq_base_url:
        pytest.skip("cq_base_url not configured in test env")
    req = httpx.Request("POST", "https://cq.test/v1/recall")
    resp = httpx.Response(500, request=req)
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.HTTPStatusError("500", request=req, response=resp))):
        out = await cq.recall("u1", "hello", metadata={"recall_scope": "people"})
    assert out["degraded"] == "error" and out["recall_scope"] == "people"
    assert out["context"] == "" and out["patch_count"] == 0
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ReadTimeout("slow", request=req))):
        out = await cq.recall("u1", "hello", metadata={})
    assert out["degraded"] == "timeout" and out["recall_scope"] == "full"


def test_degraded_recall_opens_an_incident_and_a_healthy_one_resolves_it(client_with_cq, pro_user, mock_cq, tmp_db_path):
    mock_cq["recall"].return_value = {"context": "", "matched_entities": [], "patch_count": 0,
                                      "degraded": "error", "degraded_error": "500", "recall_scope": "full"}
    r = _chat(client_with_cq, pro_user, {"project_id": "p1"})
    assert r.status_code == 200, r.text            # the turn still answers
    rows = _incidents(tmp_db_path)
    assert rows == [("scope=full", None)]           # one OPEN incident for this scope
    # Same scope again: still one incident (dedup by fingerprint), still open.
    _chat(client_with_cq, pro_user, {"project_id": "p1"})
    assert _incidents(tmp_db_path) == [("scope=full", None)]
    # Healthy recall heals it.
    mock_cq["recall"].return_value = {"context": "ctx", "matched_entities": [], "patch_count": 1, "recall_scope": "full"}
    _chat(client_with_cq, pro_user, {"project_id": "p1"})
    rows = _incidents(tmp_db_path)
    assert len(rows) == 1 and rows[0][1] is not None


def test_scopes_are_separate_incidents(client_with_cq, pro_user, mock_cq, tmp_db_path):
    mock_cq["recall"].return_value = {"context": "", "matched_entities": [], "patch_count": 0,
                                      "degraded": "error", "recall_scope": "people"}
    _chat(client_with_cq, pro_user, {"project_id": "p1"})
    mock_cq["recall"].return_value = {"context": "ok", "matched_entities": [], "patch_count": 2, "recall_scope": "full"}
    _chat(client_with_cq, pro_user, {"project_id": "p1"})
    rows = _incidents(tmp_db_path)
    assert [r[0] for r in rows] == ["scope=people"] and rows[0][1] is None  # full healing does not close people
