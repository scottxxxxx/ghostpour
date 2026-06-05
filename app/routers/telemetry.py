"""Anonymous telemetry events for app + meeting lifecycle tracking.

Wire shape (no JWT required, works pre-login):

  POST /v1/events/ping
  {
    "event_type": "app_start" | "meeting_start" | "meeting_stop",
    "device_id": "...",                  // required, iOS identifierForVendor
    "user_id":   "..." | null,           // optional, sent when logged in
    "meeting_id": "..." | null,          // optional, ties meeting_start/_stop
    "model_id":  "..." | null,           // optional, AI model used (meeting events)
    "app_version": "..." | null,         // optional
    "os_version":  "..." | null,         // optional
    "duration_seconds": int | null       // optional, computed by iOS on meeting_stop
  }
  -> 204 No Content

Storage: raw events live in `telemetry_events` with a 30-day TTL (purged at
startup; see app/database.py). Daily aggregates land in
`telemetry_daily_rollups` (kept indefinitely; computed by
app.services.telemetry_rollup).

Anti-abuse: per-IP rate limit (60 events/min/IP by default), source IP is
hashed (SHA-256) before persistence so we don't store raw IPs.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.database import get_db

router = APIRouter()

_EVENT_TYPES = ("app_start", "meeting_start", "meeting_stop")

# UUID v4-ish shape; iOS identifierForVendor is a UUID. Loose enough to
# accept any uppercased/lowercased UUID without being strict about version.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Per-IP rate limit for this endpoint. 60/min is generous for normal use
# (one event every 1s for a full minute) and tight enough to bound abuse.
_PING_RPM_PER_IP = 60


class PingEvent(BaseModel):
    event_type: Literal["app_start", "meeting_start", "meeting_stop"]
    device_id: str = Field(..., min_length=1, max_length=128)
    user_id: str | None = Field(default=None, max_length=64)
    meeting_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=32)
    os_version: str | None = Field(default=None, max_length=32)
    duration_seconds: int | None = Field(default=None, ge=0, le=86400 * 7)
    # Raw Apple sysctl `hw.machine` code (e.g. "iPhone17,3"). Server maps
    # to a marketing name at query time via app/services/device_models.py
    # so iOS doesn't need to ship a translation table.
    device_model: str | None = Field(default=None, max_length=64)
    # BCP-47ish locale string from `Locale.current.identifier` (e.g.
    # "en_US", "ja_JP"). Used for market segmentation in the dashboard.
    app_locale: str | None = Field(default=None, max_length=16)


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For (proxy chain) then
    falls back to the socket peer. Returns empty string when neither
    yields a value (e.g., test client without scope.client).
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        # First entry is the original client (proxy chain appends).
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def _ip_hash(ip: str) -> str:
    if not ip:
        return ""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


@router.post("/events/ping", status_code=204)
async def ping(
    body: PingEvent,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
) -> Response:
    """Persist a single lifecycle event. Returns 204 on success.

    No auth required by design — iOS pings before login and we want
    funnel data. Rate-limited per IP; rejects with 429 on overflow.
    """
    # Shape validation already enforced by Pydantic (Literal, length caps).
    # Extra UUID-shape check on device_id to keep the table clean.
    if not _UUID_RE.match(body.device_id):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_request",
                "message": "device_id must be a UUID",
            },
        )

    # Per-IP rate limit. Hashed IP is the bucket key so the in-memory
    # limiter and the persisted column use the same identifier.
    rate_limiter = request.app.state.rate_limiter
    ip = _client_ip(request)
    ip_h = _ip_hash(ip)
    if ip_h:
        allowed, retry_after = rate_limiter.check(f"ping:{ip_h}", _PING_RPM_PER_IP)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "rate_limited",
                    "message": f"Telemetry rate limit hit; retry in {retry_after}s",
                    "details": {"retry_after": retry_after},
                },
            )

    await db.execute(
        """INSERT INTO telemetry_events
           (id, event_type, device_id, user_id, meeting_id, model_id,
            app_version, os_version, duration_seconds, ip_hash, received_at,
            device_model, app_locale)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            body.event_type,
            body.device_id,
            body.user_id,
            body.meeting_id,
            body.model_id,
            body.app_version,
            body.os_version,
            body.duration_seconds,
            ip_h,
            datetime.now(timezone.utc).isoformat(),
            body.device_model,
            body.app_locale,
        ),
    )
    await db.commit()
    return Response(status_code=204)
