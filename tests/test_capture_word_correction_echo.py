"""A word-correction capture echoes CQ's `replaced` (SS + CQ #499, 2026-10-31).

Every capture is fire-and-forget: the client gets a status before CQ is
called. The one exception is X-CZ-Recovery: word-correction, where CQ's
`replaced` count is the only evidence the corrected transcript replaced the
old one (rule 4). Absent means unknown, never zero, so every path that does
not have CQ's number must leave the key out rather than default it.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import cq_proxy
from app.services import context_quilt as cq
from tests.test_capture_metadata_allowlist import cq_post  # noqa: F401

BODY = {"transcript": "Kore.ai said ...", "meeting_id": "m-word-fix-1"}
FIX = {"X-CZ-Recovery": "word-correction"}


def _post(client, user, headers=None):
    return client.post("/v1/capture-transcript", json=BODY,
                       headers={**user["headers"], **(headers or {})})


@pytest.mark.parametrize("n", [0, 3])
def test_cqs_replaced_comes_back_exactly(client_with_cq, pro_user, mock_cq, n):
    mock_cq["capture"].return_value = {"stored": True, "replaced": n}
    resp = _post(client_with_cq, pro_user, FIX)
    assert resp.status_code == 200
    assert resp.json() == {"status": "captured", "replaced": n}


def test_a_cq_body_without_replaced_does_not_invent_one(client_with_cq, pro_user, mock_cq):
    mock_cq["capture"].return_value = {"stored": True}
    assert _post(client_with_cq, pro_user, FIX).json() == {"status": "captured"}


def test_a_failed_cq_call_is_queued_with_no_replaced(client_with_cq, pro_user, mock_cq):
    mock_cq["capture"].return_value = None
    assert _post(client_with_cq, pro_user, FIX).json() == {"status": "queued"}


def test_a_slow_cq_is_queued_with_no_replaced(client_with_cq, pro_user, mock_cq, monkeypatch):
    monkeypatch.setattr(cq_proxy, "WORD_CORRECTION_WAIT_S", 0.05)

    async def slow(**_):
        await asyncio.sleep(1)
        return {"replaced": 1}
    mock_cq["capture"].side_effect = slow
    assert _post(client_with_cq, pro_user, FIX).json() == {"status": "queued"}


@pytest.mark.parametrize("headers", [None, {"X-CZ-Recovery": "report-404-replay"}])
def test_every_other_capture_stays_fire_and_forget(client_with_cq, pro_user, mock_cq, headers):
    mock_cq["capture"].return_value = {"replaced": 5}
    assert _post(client_with_cq, pro_user, headers).json() == {"status": "queued"}


def test_a_capture_the_gate_skips_says_skipped(client_with_cq, pro_user, mock_cq, monkeypatch):
    # Reachable only with People disabled and no free quota left; no tier
    # resolves there today, so the verdict is forced rather than staged.
    from app.services.memory_capture_policy import MemoryVerdict
    monkeypatch.setattr(cq_proxy, "resolve_memory_capture_verdict",
                        lambda **_: MemoryVerdict(verdict="skip_with_cta", cta_kind="free_no_quota_only"))
    resp = _post(client_with_cq, pro_user, FIX)
    assert resp.status_code == 200
    assert mock_cq["capture"].await_count == 0
    assert resp.json() == {"status": "skipped"}


def test_capture_returns_cqs_body(cq_post):  # noqa: F811
    cq_post.return_value = type("R", (), {"status_code": 200,
                                          "json": lambda self: {"replaced": 2},
                                          "raise_for_status": lambda self: None})()
    out = asyncio.run(cq.capture(user_id="u1", interaction_type="meeting_transcript",
                                 content="hi", recovery_source="word-correction"))
    assert out == {"replaced": 2}


def test_capture_returns_none_when_cq_fails(cq_post):  # noqa: F811
    cq_post.side_effect = RuntimeError("boom")
    assert asyncio.run(cq.capture(user_id="u1", interaction_type="meeting_transcript",
                                  content="hi")) is None
