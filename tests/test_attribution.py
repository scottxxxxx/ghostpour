"""Apple Ads install attribution: POST /v1/attribution + exchange sweep.

Ingest is anonymous-friendly and upserts on (device_id, app_id); the
authenticated token-less call links the device row to a user. The exchange
runs only in the sweep (app/services/apple_ads_attribution.py); HTTP is
patched at _post_token, the single Apple touchpoint.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _uuid() -> str:
    return str(uuid.uuid4())


def _post(client, device_id, token="tok-abc123", headers=None, **over):
    body = {"device_id": device_id, "app_version": "1.1"}
    if token is not None:
        body["attribution_token"] = token
    body.update(over)
    h = {"X-App-ID": "shouldersurf"}
    if headers:
        h.update(headers)
    return client.post("/v1/attribution", json=body, headers=h)


def _row(db_path, device_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ad_attribution WHERE device_id = ?", (device_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def test_ingest_creates_pending_row(client, tmp_db_path):
    dev = _uuid()
    r = _post(client, dev)
    assert r.status_code == 202
    assert r.json() == {"status": "received"}
    row = _row(tmp_db_path, dev)
    assert row["status"] == "pending"
    assert row["token"] == "tok-abc123"
    assert row["app_id"] == "shouldersurf"
    assert row["user_id"] is None
    assert row["app_version"] == "1.1"


def test_authed_first_call_sets_user(client, tmp_db_path, free_user):
    dev = _uuid()
    r = _post(client, dev, headers=free_user["headers"])
    assert r.status_code == 202
    assert _row(tmp_db_path, dev)["user_id"] == free_user["user_id"]


def test_link_call_attaches_user_to_anonymous_row(client, tmp_db_path, free_user):
    dev = _uuid()
    _post(client, dev)  # anonymous, token
    r = _post(client, dev, token=None, headers=free_user["headers"])  # link form
    assert r.status_code == 202
    row = _row(tmp_db_path, dev)
    assert row["user_id"] == free_user["user_id"]
    assert row["status"] == "pending"  # link call must not disturb the exchange
    assert row["token"] == "tok-abc123"


def test_upsert_no_duplicate_rows(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    _post(client, dev)
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM ad_attribution WHERE device_id = ?", (dev,)
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_link_only_call_creates_no_token_row(client, tmp_db_path, free_user):
    dev = _uuid()
    r = _post(client, dev, token=None, headers=free_user["headers"])
    assert r.status_code == 202
    row = _row(tmp_db_path, dev)
    assert row["status"] == "no_token"
    assert row["user_id"] == free_user["user_id"]


def test_invalid_device_id_rejected(client):
    r = _post(client, "not-a-uuid")
    assert r.status_code == 400


def test_completed_exchange_not_overwritten_by_new_token(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', token=NULL,"
        " campaign_id=111 WHERE device_id=?",
        (dev,),
    )
    conn.commit()
    conn.close()
    _post(client, dev, token="tok-later")
    row = _row(tmp_db_path, dev)
    assert row["status"] == "attributed"
    assert row["campaign_id"] == 111
    assert row["token"] is None


# ---------------------------------------------------------------------------
# Exchange sweep
# ---------------------------------------------------------------------------


async def _sweep(tmp_db_path):
    import aiosqlite

    from app.services.apple_ads_attribution import sweep_pending

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        return await sweep_pending(db)


_ATTRIBUTED_PAYLOAD = {
    "attribution": True,
    "orgId": 40669820,
    "campaignId": 542370539,
    "conversionType": "Download",
    "clickDate": "2026-07-21T10:01Z",
    "adGroupId": 542317095,
    "countryOrRegion": "US",
    "keywordId": 87675432,
    "adId": 542317136,
}


@pytest.mark.asyncio
async def test_sweep_persists_attributed_payload(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(200, _ATTRIBUTED_PAYLOAD),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["attributed"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "attributed"
    assert row["campaign_id"] == 542370539
    assert row["ad_group_id"] == 542317095
    assert row["keyword_id"] == 87675432
    assert row["conversion_type"] == "Download"
    assert row["country_or_region"] == "US"
    assert row["standard_payload"] == 0
    assert row["token"] is None
    assert row["exchanged_at"] is not None


@pytest.mark.asyncio
async def test_sweep_marks_organic(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(200, {"attribution": False}),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["organic"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "organic"
    assert row["attribution"] == 0
    assert row["token"] is None


@pytest.mark.asyncio
async def test_sweep_flags_standard_payload(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    placeholder = {k: (1234567890 if isinstance(v, int) else v)
                   for k, v in _ATTRIBUTED_PAYLOAD.items()}
    placeholder["attribution"] = True
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(200, placeholder),
    ):
        await _sweep(tmp_db_path)
    row = _row(tmp_db_path, dev)
    assert row["status"] == "attributed"
    assert row["standard_payload"] == 1


@pytest.mark.asyncio
async def test_sweep_retries_on_404(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(404, None),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["pending"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "pending"
    assert row["token"] == "tok-abc123"  # kept for the next sweep


@pytest.mark.asyncio
async def test_sweep_errors_on_400(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(400, None),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["error"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "error"
    assert row["token"] is None


@pytest.mark.asyncio
async def test_sweep_expires_past_ttl(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET created_at=? WHERE device_id=?", (stale, dev)
    )
    conn.commit()
    conn.close()
    exchange = AsyncMock(return_value=(200, _ATTRIBUTED_PAYLOAD))
    with patch("app.services.apple_ads_attribution._post_token", exchange):
        counts = await _sweep(tmp_db_path)
    assert counts["expired"] == 1
    exchange.assert_not_awaited()  # expired rows never hit Apple
    row = _row(tmp_db_path, dev)
    assert row["status"] == "expired"
    assert row["token"] is None


# ---------------------------------------------------------------------------
# Admin report
# ---------------------------------------------------------------------------


def test_admin_acquisition_report(client, tmp_db_path, free_user):
    dev_attr, dev_org, dev_pending = _uuid(), _uuid(), _uuid()
    _post(client, dev_attr, headers=free_user["headers"])
    _post(client, dev_org)
    _post(client, dev_pending)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', attribution=1,"
        " campaign_id=542370539, keyword_id=87675432, token=NULL"
        " WHERE device_id=?",
        (dev_attr,),
    )
    conn.execute(
        "UPDATE ad_attribution SET status='organic', attribution=0, token=NULL"
        " WHERE device_id=?",
        (dev_org,),
    )
    conn.execute(
        "UPDATE users SET ever_subscribed=1 WHERE id=?", (free_user["user_id"],)
    )
    conn.commit()
    conn.close()

    r = client.get(
        "/webhooks/admin/acquisition?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["kpis"]["total"] == 3
    assert d["kpis"]["attributed"] == 1
    assert d["kpis"]["organic"] == 1
    assert d["kpis"]["pending"] == 1
    assert d["kpis"]["linked"] == 1
    assert d["kpis"]["subscribed"] == 1
    assert d["campaigns"][0]["campaign_id"] == 542370539
    assert d["campaigns"][0]["installs"] == 1
    assert d["campaigns"][0]["subscribed"] == 1
    assert d["keywords"][0]["keyword_id"] == 87675432


def test_admin_acquisition_requires_key(client):
    r = client.get(
        "/webhooks/admin/acquisition",
        headers={"X-Admin-Key": "wrong"},
    )
    assert r.status_code == 403
