"""Apple Ads install attribution ingest.

Wire shape (works pre-login; bearer optional):

  POST /v1/attribution
  {
    "attribution_token": "...base64..." | null,   // omit on the link-only call
    "device_id": "...",                  // required, iOS identifierForVendor
    "first_launch_at": "..." | null,     // ISO8601, client clock
    "app_version": "..." | null
  }
  -> 202 {"status": "received"}

Two call forms:
  - first launch: token present, possibly anonymous. Creates the row; the
    exchange happens in the sweep daemon
    (app/services/apple_ads_attribution.py), not inline, so this endpoint
    never blocks launch on Apple.
  - link form: no token, authenticated. Attaches user_id to the device's
    existing row so attribution joins to subscription_events without
    touching the verify-receipt wire shape.

Upsert on (device_id, app_id). A completed exchange (attributed/organic) is
never overwritten by a later token. Per-app by construction via X-App-ID.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user import UserRecord
from app.routers.telemetry import _client_ip, _ip_hash, _UUID_RE

router = APIRouter()

# First launch fires exactly one call (plus maybe one link call), so this is
# purely an abuse bound, not a throughput budget.
_ATTRIBUTION_RPM_PER_IP = 30


class AttributionReport(BaseModel):
    attribution_token: str | None = Field(
        default=None, min_length=1, max_length=8192
    )
    device_id: str = Field(..., min_length=1, max_length=128)
    first_launch_at: str | None = Field(default=None, max_length=40)
    app_version: str | None = Field(default=None, max_length=32)


@router.post("/attribution", status_code=202)
async def report_attribution(
    body: AttributionReport,
    request: Request,
    user: UserRecord | None = Depends(get_current_user_optional),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Upsert the device's attribution row. Fire-and-forget from iOS."""
    if not _UUID_RE.match(body.device_id):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_request",
                "message": "device_id must be a UUID",
            },
        )

    rate_limiter = request.app.state.rate_limiter
    ip_h = _ip_hash(_client_ip(request))
    if ip_h:
        allowed, retry_after = rate_limiter.check(
            f"attribution:{ip_h}", _ATTRIBUTION_RPM_PER_IP
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "rate_limited",
                    "message": f"Attribution rate limit hit; retry in {retry_after}s",
                    "details": {"retry_after": retry_after},
                },
            )

    app_id = getattr(request.state, "app_id", "unknown")
    now = datetime.now(timezone.utc).isoformat()

    cur = await db.execute(
        "SELECT id, status FROM ad_attribution WHERE device_id = ? AND app_id = ?",
        (body.device_id, app_id),
    )
    row = await cur.fetchone()

    if row is None:
        await db.execute(
            """INSERT INTO ad_attribution
               (id, device_id, app_id, user_id, status, token,
                app_version, first_launch_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                body.device_id,
                app_id,
                user.id if user else None,
                "pending" if body.attribution_token else "no_token",
                body.attribution_token,
                body.app_version,
                body.first_launch_at,
                now,
            ),
        )
    else:
        if user is not None:
            await db.execute(
                "UPDATE ad_attribution SET user_id = ? WHERE id = ? AND user_id IS NULL",
                (user.id, row["id"]),
            )
        if body.attribution_token and row["status"] not in ("attributed", "organic"):
            await db.execute(
                "UPDATE ad_attribution SET token = ?, status = 'pending' WHERE id = ?",
                (body.attribution_token, row["id"]),
            )
    await db.commit()
    return {"status": "received"}
