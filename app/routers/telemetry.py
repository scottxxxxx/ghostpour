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
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.database import get_db

router = APIRouter()

_EVENT_TYPES = ("app_start", "meeting_start", "meeting_stop",
                "onboarding_completed")

# UUID v4-ish shape; iOS identifierForVendor is a UUID. Loose enough to
# accept any uppercased/lowercased UUID without being strict about version.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Per-IP rate limit for this endpoint. 60/min is generous for normal use
# (one event every 1s for a full minute) and tight enough to bound abuse.
_PING_RPM_PER_IP = 60


class OnboardingStep(BaseModel):
    """One onboarding page and how long the user dwelled on it. `step` is a
    canonical id from the agreed vocabulary (see the wire contract). Dwell is
    measured off the pager, paused on backgrounding."""
    step: str = Field(..., min_length=1, max_length=48)
    dwell_ms: int = Field(..., ge=0, le=86_400_000)  # 24h/step sanity cap


class OnboardingPayload(BaseModel):
    """First-run onboarding funnel outcome. All behavioral, no PII: the name
    and the voice-enrollment audio never leave the device, only booleans.
    Carried on event_type='onboarding_completed', flushed on background if
    onboarding is still in progress so we capture drop-off, not just
    completers. Joined to conversion on device_id."""
    total_duration_ms: int | None = Field(default=None, ge=0, le=7 * 86_400_000)
    completed: bool                                  # completed vs abandoned
    tour_skipped: bool = False
    name_provided: bool = False
    voice_enrolled: bool = False
    auth_choice: Literal["apple", "on_device"] | None = None
    abandoned_at_step: str | None = Field(default=None, max_length=48)
    steps: list[OnboardingStep] = Field(default_factory=list, max_length=40)


class PingEvent(BaseModel):
    event_type: Literal["app_start", "meeting_start", "meeting_stop",
                        "onboarding_completed"]
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
    # Onboarding funnel outcome, present only on event_type=='onboarding_completed'.
    onboarding: OnboardingPayload | None = None
    # Distribution channel, from StoreKit 2 AppTransaction.environment
    # (Apple-signed, present for every install): "production" = App Store,
    # "sandbox" = TestFlight, "xcode" = local dev. Lets the dashboard split
    # TestFlight vs App Store usage. Optional so older builds still validate.
    distribution: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _require_onboarding_payload(self):
        # The onboarding block is mandatory for its event and meaningless on
        # the lifecycle events; keep the two shapes from bleeding into each
        # other.
        if self.event_type == "onboarding_completed" and self.onboarding is None:
            raise ValueError("onboarding payload required for onboarding_completed")
        if self.event_type != "onboarding_completed" and self.onboarding is not None:
            raise ValueError("onboarding payload only valid on onboarding_completed")
        return self


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

    # Onboarding funnel: a richer, distinct event that lands in its own table
    # (one row per finished-or-abandoned onboarding), keyed by device_id for
    # the conversion join. No geo needed (first-run, not geo-targeted), so
    # branch before the lookup.
    if body.event_type == "onboarding_completed":
        ob = body.onboarding
        await db.execute(
            """INSERT INTO onboarding_events
               (id, device_id, app_id, received_at, total_duration_ms,
                completed, tour_skipped, name_provided, voice_enrolled,
                auth_choice, abandoned_at_step, steps, app_version,
                os_version, device_model, app_locale, distribution)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                body.device_id,
                getattr(request.state, "app_id", "unknown"),
                datetime.now(timezone.utc).isoformat(),
                ob.total_duration_ms,
                int(ob.completed),
                int(ob.tour_skipped),
                int(ob.name_provided),
                int(ob.voice_enrolled),
                ob.auth_choice,
                ob.abandoned_at_step,
                json.dumps([s.model_dump() for s in ob.steps]),
                body.app_version,
                body.os_version,
                body.device_model,
                body.app_locale,
                body.distribution,
            ),
        )
        await db.commit()
        return Response(status_code=204)

    # Derive coarse geo from the raw IP, then it's discarded (only the hash and
    # the derived country/region/city persist; never the raw IP, no lat/long).
    # City collection approved 2026-07-08 (#318 §9) — targeting on it is
    # guarded by the min-audience floor at campaign authoring and resolve.
    from app.services import geoip
    geo = geoip.lookup(ip) or {}

    await db.execute(
        """INSERT INTO telemetry_events
           (id, event_type, device_id, user_id, meeting_id, model_id,
            app_version, os_version, duration_seconds, ip_hash, received_at,
            device_model, app_locale, app_id, country, region, city,
            distribution)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            getattr(request.state, "app_id", "unknown"),
            geo.get("country"),
            geo.get("region"),
            geo.get("city"),
            body.distribution,
        ),
    )
    await db.commit()
    return Response(status_code=204)
