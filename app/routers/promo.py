"""Promo runtime (MVP) — serve creative, resolve a campaign on launch, ingest events.

This is the thin runtime on top of the campaign store (#293) and the CTA/cta_id
contract (#306). The decision engine is intentionally minimal for the first
sanity-check slice: pick the highest-priority active campaign for the requesting
app whose targeting matches, weighted-pick a variant (stable per device), and
let the client report impression/click/dismiss/convert. App scoping is by
`app_id` (X-App-ID), so a campaign only resolves for its own app — SS today,
TR later with no code change.

See docs/design/gp-promo-decision-engine.md. Reporting funnel: GET
/webhooks/admin/campaign/{id}/report.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord

router = APIRouter()

_PROMO_ASSET_DIR = (Path(__file__).parent.parent / "static" / "promo").resolve()
_PROMO_EVENT_TYPES = {"impression", "dismiss", "click", "convert"}
_CAMPAIGN_JSON_COLS = ("targeting", "frequency", "placements", "variants")


def _parse_campaign(row) -> dict:
    """DB row -> dict with the JSON columns parsed (local copy to stay decoupled
    from the admin CRUD module)."""
    d = dict(row)
    for col in _CAMPAIGN_JSON_COLS:
        raw = d.get(col)
        try:
            d[col] = json.loads(raw) if raw else ({} if col in ("targeting", "frequency") else [])
        except (json.JSONDecodeError, TypeError):
            d[col] = {} if col in ("targeting", "frequency") else []
    return d


def _targeting_matches(targeting: dict, user: UserRecord) -> bool:
    """MVP targeting: a `users` allowlist (email or user id) and a `tiers`
    allowlist. Empty/absent rule = no constraint on that dimension."""
    users = targeting.get("users")
    if users:
        identity = {user.id}
        if user.email:
            identity.add(user.email)
        if not identity.intersection(users):
            return False
    tiers = targeting.get("tiers")
    if tiers and user.tier not in tiers:
        return False
    return True


def _pick_variant(variants: list, device_id: str, campaign_id: str) -> dict | None:
    """Weighted pick, stable per device so a user sees the same variant across
    launches (clean A/B buckets)."""
    weighted = [(v, max(int(v.get("weight", 0)), 0)) for v in variants if isinstance(v, dict)]
    total = sum(w for _, w in weighted)
    if total <= 0:
        return weighted[0][0] if weighted else None
    bucket = int(hashlib.sha256(f"{campaign_id}:{device_id}".encode()).hexdigest(), 16) % total
    acc = 0
    for variant, weight in weighted:
        acc += weight
        if bucket < acc:
            return variant
    return weighted[-1][0]


@router.get("/promo/assets/{name}")
async def serve_promo_asset(name: str):
    """Public: serve a promo HTML creative — the target of a variant's html_url."""
    if not name.endswith(".html") or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=404, detail="not found")
    path = (_PROMO_ASSET_DIR / name).resolve()
    if path.parent != _PROMO_ASSET_DIR or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path, media_type="text/html",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/promo/resolve")
async def resolve_promo(
    request: Request,
    device_id: str = Query(..., min_length=1),
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Launch-ping: return the promo to show for this app/user/device, or {}.

    App-scoped by X-App-ID. Highest priority active in-window campaign whose
    targeting matches and whose per-device frequency cap isn't spent.
    """
    app_id = getattr(request.state, "app_id", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    cur = await db.execute(
        "SELECT * FROM promo_campaigns WHERE app_id = ? AND status = 'active' "
        "ORDER BY priority DESC, updated_at DESC",
        (app_id,),
    )
    rows = await cur.fetchall()
    for row in rows:
        c = _parse_campaign(row)
        if c.get("starts_at") and now < c["starts_at"]:
            continue
        if c.get("expires_at") and now > c["expires_at"]:
            continue
        if not _targeting_matches(c.get("targeting") or {}, user):
            continue
        max_impressions = (c.get("frequency") or {}).get("max_impressions")
        if max_impressions:
            pres = await (await db.execute(
                "SELECT shown_count FROM promo_presentations WHERE device_id = ? AND campaign_id = ?",
                (device_id, c["id"]),
            )).fetchone()
            if pres and pres["shown_count"] >= max_impressions:
                continue
        variant = _pick_variant(c.get("variants") or [], device_id, c["id"])
        if not variant:
            continue
        return {"campaign_id": c["id"], "variant": variant}
    return {}


class PromoEventBody(BaseModel):
    event_type: str                 # impression | dismiss | click | convert
    campaign_id: str
    device_id: str
    variant_id: str | None = None
    cta_id: str | None = None       # which CTA was tapped (click)
    visible_ms: int | None = None   # impression/dismiss dwell


@router.post("/promo/events", status_code=204)
async def ingest_promo_event(
    body: PromoEventBody,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Client-reported promo telemetry. Writes promo_events and advances the
    per-device presentations row (frequency / cooldown / convert state)."""
    if body.event_type not in _PROMO_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"event_type must be one of {sorted(_PROMO_EVENT_TYPES)}")
    app_id = getattr(request.state, "app_id", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO promo_events
           (id, created_at, device_id, user_id, campaign_id, variant_id, app_id,
            event_type, visible_ms, cta_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, now, body.device_id, user.id, body.campaign_id,
         body.variant_id, app_id, body.event_type, body.visible_ms, body.cta_id),
    )
    if body.event_type == "impression":
        await db.execute(
            """INSERT INTO promo_presentations
               (device_id, campaign_id, variant_id, app_id, shown_count, first_shown_at, last_shown_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(device_id, campaign_id) DO UPDATE SET
                 shown_count = shown_count + 1,
                 last_shown_at = excluded.last_shown_at,
                 variant_id = excluded.variant_id""",
            (body.device_id, body.campaign_id, body.variant_id, app_id, now, now),
        )
    else:
        col = {"click": "last_clicked_at", "dismiss": "last_dismissed_at", "convert": "converted_at"}[body.event_type]
        await db.execute(
            f"UPDATE promo_presentations SET {col} = ? WHERE device_id = ? AND campaign_id = ?",
            (now, body.device_id, body.campaign_id),
        )
    await db.commit()
    return Response(status_code=204)
