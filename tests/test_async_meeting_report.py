"""Async meeting reports (SS + GP contract, 2026-10-01).

A 93 minute meeting (44381CC7) lost its report: the client POSTs with a 90 s
timeout, iOS suspended the backgrounded app about 18 s in, and the cached GET
13 minutes later was a bare 404. With `"async": true` the POST answers 202 and
GP finishes server side whatever the client does; GET tells "still cooking"
(202) from "failed" (422, with a code) from "never started" (404).

Also pinned here: the report output ceiling (4096 cut a real report off at
finish_reason max_tokens, which is a parse error with nothing cached).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from unittest.mock import AsyncMock

import asyncio

from app.models.chat import ChatResponse
from tests.test_report_write_build_floor import MODEL_JSON, _seed

GENERATING_KEYS = {"status", "started_at", "poll_after_seconds", "expected_seconds"}


def _headers(user):
    return {**user["headers"], "X-App-ID": "shouldersurf"}


def _wire(monkeypatch, *, text=None, gate: threading.Event | None = None):
    calls = []

    async def fake_route(chat_request):
        calls.append(chat_request)
        if gate is not None:
            # Bounded: if the job is (wrongly) run inline, this must fail the
            # test rather than hang it, so the wait gives up after 2 s.
            end = time.monotonic() + 2
            while not gate.is_set() and time.monotonic() < end:
                await asyncio.sleep(0.01)
        return ChatResponse(text=text if text is not None else json.dumps(MODEL_JSON),
                            input_tokens=10, output_tokens=20,
                            model="claude-sonnet-4-6", provider="anthropic",
                            usage={"input_tokens": 10, "output_tokens": 20})

    from app.main import app as _app
    monkeypatch.setattr(_app.state.provider_router, "route", AsyncMock(side_effect=fake_route))
    return calls


def _post(client, user, meeting_id, **body):
    return client.post(f"/v1/meetings/{meeting_id}/report",
                       json={"duration_seconds": 600, **body}, headers=_headers(user))


def _get(client, user, meeting_id):
    return client.get(f"/v1/meetings/{meeting_id}/report", headers=_headers(user))


def _poll(client, user, meeting_id, timeout=5.0):
    end = time.monotonic() + timeout
    while True:
        r = _get(client, user, meeting_id)
        if r.status_code != 202 or time.monotonic() > end:
            return r
        time.sleep(0.02)


def _job_rows(db_path, meeting_id):
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM report_jobs WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]
    conn.close()
    return n


def test_async_post_answers_202_and_the_report_arrives_by_get(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id, **{"async": True})
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == GENERATING_KEYS
    assert body["status"] == "generating" and body["poll_after_seconds"] == 5
    assert "+00:00" in body["started_at"]  # ISO 8601 with offset
    done = _poll(client, pro_user, meeting_id)
    assert done.status_code == 200, done.text
    assert done.json()["report_json"]["header"]["title"] == "t"
    assert len(calls) == 1
    assert _job_rows(tmp_db_path, meeting_id) == 0  # success leaves no job row


def test_a_second_async_post_while_running_is_the_same_job(client, pro_user, tmp_db_path, monkeypatch):
    gate = threading.Event()
    calls = _wire(monkeypatch, gate=gate)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    first = _post(client, pro_user, meeting_id, **{"async": True})
    second = _post(client, pro_user, meeting_id, **{"async": True})
    assert first.status_code == second.status_code == 202
    assert first.json()["started_at"] == second.json()["started_at"]
    running = _get(client, pro_user, meeting_id)
    assert running.status_code == 202 and set(running.json()) == GENERATING_KEYS
    gate.set()
    assert _poll(client, pro_user, meeting_id).status_code == 200
    assert len(calls) == 1


def test_a_failed_job_is_terminal_with_a_code_not_a_bare_404(client, pro_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch, text='{"header": {"title": "cut off at max_tok')
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    assert _post(client, pro_user, meeting_id, **{"async": True}).status_code == 202
    r = _poll(client, pro_user, meeting_id)
    assert r.status_code == 422, r.text
    body = r.json()
    assert set(body) == {"status", "started_at", "code", "message", "previous_report_available"}
    assert body["status"] == "failed" and body["code"] == "report_parse_error"
    assert body["previous_report_available"] is False


def test_a_running_row_this_process_is_not_running_is_lost_to_restart(client, pro_user, tmp_db_path):
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("INSERT INTO report_jobs (meeting_id, user_id, status, started_at) VALUES (?,?,?,?)",
                 (meeting_id, pro_user["user_id"], "running", "2026-10-01T17:59:42+00:00"))
    conn.commit(); conn.close()
    r = _get(client, pro_user, meeting_id)
    assert r.status_code == 422 and r.json()["code"] == "lost_to_restart"


def test_a_regenerate_in_flight_beats_the_cached_report(client, pro_user, tmp_db_path, monkeypatch):
    gate = threading.Event()
    _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    assert _post(client, pro_user, meeting_id).status_code == 200  # an old report is cached
    _wire(monkeypatch, gate=gate)
    assert _post(client, pro_user, meeting_id, **{"async": True}).status_code == 202
    assert _get(client, pro_user, meeting_id).status_code == 202
    gate.set()
    assert _poll(client, pro_user, meeting_id).status_code == 200


def test_a_failed_regenerate_says_an_older_report_exists(client, pro_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    assert _post(client, pro_user, meeting_id).status_code == 200
    _wire(monkeypatch, text="not json")
    _post(client, pro_user, meeting_id, **{"async": True})
    r = _poll(client, pro_user, meeting_id)
    assert r.status_code == 422 and r.json()["previous_report_available"] is True


def test_a_synchronous_success_clears_an_earlier_failed_job(client, pro_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch, text="not json")
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    _post(client, pro_user, meeting_id, **{"async": True})
    assert _poll(client, pro_user, meeting_id).status_code == 422
    _wire(monkeypatch)
    assert _post(client, pro_user, meeting_id).status_code == 200
    assert _get(client, pro_user, meeting_id).status_code == 200


def test_without_async_the_post_is_synchronous_as_before(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, meeting_id)
    assert r.status_code == 200 and "report_html" in r.json()
    assert len(calls) == 1 and _job_rows(tmp_db_path, meeting_id) == 0


def test_cheap_checks_stay_synchronous_on_an_async_post(client, pro_user, monkeypatch):
    calls = _wire(monkeypatch)
    r = _post(client, pro_user, "never-captured", **{"async": True})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_meeting_data"
    assert calls == []


def test_report_output_ceiling_is_8192(client, pro_user, tmp_db_path, monkeypatch):
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, pro_user["user_id"])
    _post(client, pro_user, meeting_id)
    assert calls[0].max_tokens == 8192


def test_every_locale_serves_the_async_flag():
    import glob
    for f in glob.glob("config/remote/client-config*.json"):
        assert json.load(open(f))["post_session"]["report_async"] is True, f


def test_a_budget_blocked_async_report_arrives_as_the_canned_report(client, exhausted_user, tmp_db_path, monkeypatch):
    # The budget gate runs in the job (it needs the built prompt), so the
    # async POST is a 202 and GET serves the same placeholder the sync path
    # would have: never a 422, never a model call.
    calls = _wire(monkeypatch)
    meeting_id = _seed(tmp_db_path, exhausted_user["user_id"])
    assert _post(client, exhausted_user, meeting_id, **{"async": True}).status_code == 202
    r = _poll(client, exhausted_user, meeting_id)
    assert r.status_code == 200, r.text
    assert r.json()["report_status"] == "placeholder_budget_blocked"
    assert r.json()["is_editable"] is False
    assert calls == []
