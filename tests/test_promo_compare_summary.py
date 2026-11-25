"""Promo metering for SS's "compare summaries" call (Scott, 2026-11-25).

SS sends its normal Generate summary request with metadata.promo =
"compare_summary". It is served free once per account per UTC month, never
touches the allowance, is refused for anonymous accounts, and a second try in
the month is a 429 promo_exhausted. See app/services/promo_metering.py.

Assertions are on stored state (the meter, usage_log, promo_redemptions) and
on the echo the client reads, not on a 200 alone.
"""

import asyncio
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatResponse
from tests.conftest import _insert_user, _jwt_token, chat_request

SS = {"X-App-ID": "shouldersurf"}


def _summary(promo="compare_summary", call_type="summary", **meta):
    md = {"call_type": call_type, "prompt_mode": "ReanalyzeSummary",
          "meeting_id": f"m-{uuid.uuid4().hex[:8]}"}
    if promo is not None:
        md["promo"] = promo
    md.update(meta)
    return chat_request(user_content="Transcript: we agreed to ship Friday.", metadata=md)


def _meter(db_path, user_id) -> float:
    conn = sqlite3.connect(db_path)
    v = conn.execute("SELECT monthly_used_usd FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    conn.close()
    return float(v or 0)


def _rows(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT call_type, status, promo, estimated_cost_usd FROM usage_log WHERE user_id = ?",
        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _user(db_path, user_id="promo-user", monthly_used=0.0):
    from app.models.tier import load_tier_config
    free_limit = load_tier_config("config/tiers.yml").tiers["free"].monthly_cost_limit_usd
    _insert_user(db_path, user_id=user_id, tier="free", monthly_limit=free_limit,
                 monthly_used=monthly_used)
    return {**SS, "Authorization": f"Bearer {_jwt_token(user_id)}"}, free_limit


def _at(dt):
    return patch("app.services.promo_metering._now", return_value=dt)


# --- 1. first promo is free, even on an exhausted allowance ------------------

def test_first_promo_is_served_without_touching_an_exhausted_allowance(client, tmp_db_path):
    hdr, limit = _user(tmp_db_path, monthly_used=999.0)
    before = _meter(tmp_db_path, "promo-user")
    assert before >= limit, "fixture must start exhausted"

    r = client.post("/v1/chat", headers=hdr, json=_summary())

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Test response from mock provider."
    assert "feature_state" not in body or not body["feature_state"]
    assert body["promo"] == {"code": "compare_summary", "applied": True, "remaining": 0}
    assert r.headers["X-Promo"] == "compare_summary; remaining=0"
    assert _meter(tmp_db_path, "promo-user") == before
    rows = [x for x in _rows(tmp_db_path, "promo-user") if x["call_type"] == "summary"]
    assert len(rows) == 1 and rows[0]["promo"] == "compare_summary"
    # GP's own cost is still on the row.
    assert rows[0]["estimated_cost_usd"] and rows[0]["estimated_cost_usd"] > 0


# --- 2. the second promo in the month is 429 ---------------------------------

def test_second_promo_in_the_month_is_429_promo_exhausted(client, tmp_db_path, mock_provider):
    hdr, _ = _user(tmp_db_path)
    with _at(datetime(2026, 11, 10, 12, tzinfo=timezone.utc)):
        assert client.post("/v1/chat", headers=hdr, json=_summary()).status_code == 200
        calls = mock_provider.await_count
        meter = _meter(tmp_db_path, "promo-user")
        r = client.post("/v1/chat", headers=hdr, json=_summary())
    assert r.status_code == 429, r.text
    d = r.json()["detail"]
    assert d["code"] == "promo_exhausted"
    assert d["details"]["promo"] == "compare_summary" and d["details"]["remaining"] == 0
    assert d["details"]["resets_at"] == "2026-12-01T00:00:00+00:00"
    # Not quietly served as a metered call either.
    assert mock_provider.await_count == calls
    assert _meter(tmp_db_path, "promo-user") == meter


# --- 3. a new UTC month resets it --------------------------------------------

def test_a_new_month_resets_the_promo(client, tmp_db_path):
    hdr, _ = _user(tmp_db_path)
    with _at(datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)):
        assert client.post("/v1/chat", headers=hdr, json=_summary()).status_code == 200
        assert client.post("/v1/chat", headers=hdr, json=_summary()).status_code == 429
    with _at(datetime(2026, 11, 1, 0, 0, 1, tzinfo=timezone.utc)):
        r = client.post("/v1/chat", headers=hdr, json=_summary())
    assert r.status_code == 200, r.text
    assert r.headers["X-Promo"] == "compare_summary; remaining=0"


# --- 4. anonymous accounts get 403 -------------------------------------------

def test_anonymous_account_gets_403_promo_requires_sign_in(client, tmp_db_path, mock_provider):
    a = client.post("/auth/anonymous", json={"install_id": str(uuid.uuid4())}, headers=SS).json()
    hdr = {**SS, "Authorization": f"Bearer {a['access_token']}"}
    calls = mock_provider.await_count
    r = client.post("/v1/chat", headers=hdr, json=_summary())
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "promo_requires_sign_in"
    assert mock_provider.await_count == calls
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0]
    conn.close()
    assert n == 0


def test_signed_out_is_401_before_any_promo_logic(client):
    r = client.post("/v1/chat", headers=SS, json=_summary())
    assert r.status_code == 401


# --- 5. promo on a non-summary call is metered normally ----------------------

def test_promo_on_a_non_summary_call_is_ignored_and_metered(client, tmp_db_path, caplog):
    hdr, _ = _user(tmp_db_path)
    before = _meter(tmp_db_path, "promo-user")
    with caplog.at_level("INFO", logger="cloudzap.promo_metering"):
        r = client.post("/v1/chat", headers=hdr, json=_summary(call_type="query"))
    assert r.status_code == 200, r.text
    assert "promo" not in r.json() and "X-Promo" not in r.headers
    assert _meter(tmp_db_path, "promo-user") > before
    assert all(x["promo"] is None for x in _rows(tmp_db_path, "promo-user"))
    assert any("promo_ignored" in m and "wrong_call_type" in m for m in caplog.messages)


def test_unknown_promo_value_is_ignored_and_metered(client, tmp_db_path, caplog):
    hdr, _ = _user(tmp_db_path)
    before = _meter(tmp_db_path, "promo-user")
    with caplog.at_level("INFO", logger="cloudzap.promo_metering"):
        r = client.post("/v1/chat", headers=hdr, json=_summary(promo="free_lunch"))
    assert r.status_code == 200, r.text
    assert "X-Promo" not in r.headers
    assert _meter(tmp_db_path, "promo-user") > before
    assert any("unknown_promo" in m for m in caplog.messages)


def test_a_client_cannot_stamp_its_own_row_as_promo(client, tmp_db_path):
    hdr, _ = _user(tmp_db_path)
    r = client.post("/v1/chat", headers=hdr,
                    json=_summary(promo=None, promo_applied="compare_summary"))
    assert r.status_code == 200, r.text
    assert all(x["promo"] is None for x in _rows(tmp_db_path, "promo-user"))


# --- 6. the allowance sum excludes promo rows --------------------------------

def test_allowance_real_spend_sum_excludes_promo_rows(client, tmp_db_path):
    # `client` runs the app's lifespan, which migrates the test database.
    from app.services.allocation_reset import real_spend_alerts

    async def go():
        _insert_user(tmp_db_path, user_id="spend-user", tier="free", monthly_limit=1.0)
        conn = sqlite3.connect(tmp_db_path)
        now = datetime.now(timezone.utc).isoformat()
        for cost, promo in ((0.50, None), (0.45, "compare_summary")):
            conn.execute(
                """INSERT INTO usage_log (id, user_id, provider, model, estimated_cost_usd,
                       request_timestamp, status, call_type, promo)
                   VALUES (?, 'spend-user', 'anthropic', 'm', ?, ?, 'success', 'summary', ?)""",
                (uuid.uuid4().hex, cost, now, promo))
        conn.commit()
        conn.close()
        import aiosqlite
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            return await real_spend_alerts(db, datetime.now(timezone.utc), threshold=0.8)

    alerts = asyncio.run(go())
    # 0.50 of a 1.00 cap is under the 0.8 threshold. Counting the 0.45 promo
    # row would put it at 0.95 and raise an alert.
    assert [a for a in alerts if a["user_id"] == "spend-user"] == []


def test_record_cost_skips_the_meter_for_a_promo(client, tmp_db_path):
    from app.models.tier import load_tier_config
    from app.services.usage_tracker import UsageTracker

    tier = load_tier_config("config/tiers.yml").tiers["free"]

    async def go():
        _insert_user(tmp_db_path, user_id="meter-user", tier="free", monthly_limit=1.0)
        import aiosqlite
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            t = UsageTracker()
            await t.record_cost(db, "meter-user", 0.25, tier, app_id="shouldersurf",
                                promo="compare_summary")
            await t.record_cost(db, "meter-user", 0.10, tier, app_id="shouldersurf")

    asyncio.run(go())
    assert _meter(tmp_db_path, "meter-user") == pytest.approx(0.10)


# --- 7. two concurrent promo taps give exactly one free ----------------------

def test_two_concurrent_promo_calls_give_exactly_one_free(client, tmp_db_path, mock_provider):
    hdr, _ = _user(tmp_db_path)
    canned = mock_provider.canned_response

    async def slow_route(*a, **k):
        await asyncio.sleep(0.4)
        return canned.model_copy()

    mock_provider.side_effect = slow_route
    results: list = []
    barrier = threading.Barrier(2)

    def tap():
        barrier.wait()
        results.append(client.post("/v1/chat", headers=hdr, json=_summary()))

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 429], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 429)
    assert loser.json()["detail"]["code"] == "promo_exhausted"
    promo_rows = [x for x in _rows(tmp_db_path, "promo-user") if x["promo"]]
    assert len(promo_rows) == 1


# --- 8. an upstream failure does not use the month's promo -------------------

def test_upstream_failure_does_not_count_toward_the_cap(client, tmp_db_path):
    from fastapi import HTTPException
    hdr, _ = _user(tmp_db_path)
    with patch("app.services.anthropic_or_fallback.route_with_fallback",
               AsyncMock(side_effect=HTTPException(status_code=502, detail={
                   "code": "provider_error", "message": "upstream down"}))):
        r = client.post("/v1/chat", headers=hdr, json=_summary())
    assert r.status_code == 502, r.text
    r = client.post("/v1/chat", headers=hdr, json=_summary())
    assert r.status_code == 200, r.text
    assert r.headers["X-Promo"] == "compare_summary; remaining=0"
