"""G1 N-400 choice certainty gate (Scott approved 2026-11-18). GP passes
Jev's numbers through with NO floor, falls through on anything short of a
clean answer, is marital-only, and never logs the question or reply."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.routers import n400_jev_gate as g
from app.services import typesafe_judge as tj

URL = "/v1/n400/jev-gate"
BODY = {"case_id": "c1", "node_id": "q_p5_marital_status", "locale": "en",
        "question": "What is your current marital status?",
        "reply": "SECRETREPLY married but separated"}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """A fresh breaker, and no recorded rows reaching a database."""
    monkeypatch.setattr(tj, "breaker", tj.Breaker())
    monkeypatch.setattr(tj, "record_later", lambda row, app_id: None)
    tj._LIVE.clear()
    yield
    tj._LIVE.clear()


def _jev(status="qualified_or_unsure", sconf=0.41, vol="status_only"):
    return {"answers": {
        "status": {"type": "choice", "choice": status, "confidence": sconf,
                   "probabilities": {"one_status_plainly": 0.30, "qualified_or_unsure": 0.41,
                                     "more_than_one_status": 0.28, "no_status": 0.01}},
        "volunteers": {"type": "choice", "choice": vol, "confidence": 0.9,
                       "probabilities": {"gives_other_answers": 0.1, "status_only": 0.9}}},
        "usage": {"input_tokens": 680}}


def _key(v="k"):
    s = g.get_settings()
    return patch.object(s, "typesafe_api_key", v)


def test_low_confidence_passes_through_with_no_floor(client, free_user, caplog):
    caplog.set_level(logging.INFO, logger="ghostpour.n400_jev_gate")
    with _key(), patch.object(tj, "ask", new=AsyncMock(return_value=_jev())) as ask:
        r = client.post(URL, json=BODY, headers=free_user["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["code"] == "ok"
    assert d["status"]["choice"] == "qualified_or_unsure" and d["status"]["confidence"] == 0.41
    assert d["status"]["probabilities"]["more_than_one_status"] == 0.28
    assert d["volunteers"]["choice"] == "status_only"
    state = ask.await_args.args[1]
    assert state == "Form question: What is your current marital status? Her reply: SECRETREPLY married but separated"
    assert ask.await_args.kwargs["timeout"] == 1.5
    assert "SECRETREPLY" not in caplog.text and "marital status?" not in caplog.text


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), RuntimeError("boom")])
def test_timeout_or_error_falls_through(client, free_user, exc):
    with _key(), patch.object(tj, "ask", new=AsyncMock(side_effect=exc)):
        r = client.post(URL, json=BODY, headers=free_user["headers"])
    assert r.json() == {"code": "fallthrough"}


def test_missing_key_and_other_nodes_fall_through_without_calling_jev(client, free_user):
    with patch.object(tj, "ask", new=AsyncMock(return_value=_jev())) as ask:
        with _key(""):
            assert client.post(URL, json=BODY, headers=free_user["headers"]).json() == {"code": "fallthrough"}
        with _key():
            r = client.post(URL, json={**BODY, "node_id": "q_p6_eye_color"}, headers=free_user["headers"])
            assert r.json() == {"code": "fallthrough"}
    ask.assert_not_awaited()


def test_an_unknown_label_falls_through(client, free_user):
    with _key(), patch.object(tj, "ask", new=AsyncMock(return_value=_jev(status="divorced"))):
        assert client.post(URL, json=BODY, headers=free_user["headers"]).json() == {"code": "fallthrough"}


def test_needs_auth(client):
    assert client.post(URL, json=BODY).status_code in (401, 403)
