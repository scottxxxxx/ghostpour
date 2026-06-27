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

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user import UserRecord
from app.services import promo_assets

router = APIRouter()

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


# Targeting dims that need the server-built device profile (telemetry lookup).
_PROFILE_KEYS = {"locales", "app_version", "meetings_recorded", "active_within_days", "device_families", "geo"}


def _semver(v: str) -> tuple:
    parts = []
    for p in str(v or "").split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _version_in_range(version: str | None, rng: dict) -> bool:
    if not version:
        return False
    v = _semver(version)
    mn, mx = rng.get("min"), rng.get("max")
    if mn and v < _semver(mn):
        return False
    if mx and v > _semver(mx):
        return False
    return True


def _locale_matches(locale: str | None, targets: list) -> bool:
    if not locale:
        return False
    loc = locale.lower().replace("-", "_")
    lang = loc.split("_")[0]
    return any(loc == str(t).lower() or lang == str(t).lower() or loc.startswith(str(t).lower()) for t in targets)


def _device_family_matches(device: str | None, families: list) -> bool:
    if not device:
        return False
    norm = "".join(device.lower().split())  # "iPhone 16 Pro Max" -> "iphone16promax"
    return any(norm.startswith("".join(str(f).lower().split())) for f in families)


def _within_days(ts: str, days: int) -> bool:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() <= days * 86400
    except Exception:
        return False


async def _device_profile(db: aiosqlite.Connection, device_id: str, user: UserRecord | None) -> dict:
    """Build the device targeting profile from telemetry — no client change.
    Latest locale / app_version / device family, lifetime meeting count, and
    last-active recency; tier / signed-in from the optional bearer user."""
    from app.services.device_models import to_marketing_name
    prof = {
        "locale": None, "app_version": None, "device": None,
        "meetings_recorded": 0, "last_active": None,
        "country": None, "region": None,
        "tier": user.tier if user else None, "signed_in": user is not None,
    }
    latest = await (await db.execute(
        "SELECT app_locale, app_version, device_model, received_at, country, region "
        "FROM telemetry_events WHERE device_id = ? ORDER BY received_at DESC LIMIT 1", (device_id,)
    )).fetchone()
    if latest:
        prof["locale"] = latest["app_locale"]
        prof["app_version"] = latest["app_version"]
        prof["device"] = to_marketing_name(latest["device_model"])
        prof["last_active"] = latest["received_at"]
        prof["country"] = latest["country"]
        prof["region"] = latest["region"]
    agg = await (await db.execute(
        "SELECT SUM(CASE WHEN event_type = 'meeting_start' THEN 1 ELSE 0 END) AS meetings, "
        "MAX(received_at) AS last_active FROM telemetry_events WHERE device_id = ?", (device_id,)
    )).fetchone()
    if agg:
        prof["meetings_recorded"] = agg["meetings"] or 0
        prof["last_active"] = agg["last_active"] or prof["last_active"]
    return prof


def _geo_constraint(targeting: dict) -> tuple[list, list]:
    """(countries, regions) the campaign's geo block constrains on. Either may be
    empty (no constraint on that level). Absent geo block => both empty."""
    g = targeting.get("geo") or {}
    return list(g.get("countries") or []), list(g.get("regions") or [])


async def _geo_audience(db: aiosqlite.Connection, countries: list, regions: list) -> int:
    """Distinct devices seen (within telemetry retention) whose stored geo matches
    the constraint — the privacy floor for `min_audience`. country/region are
    ANDed when both are present, matching _targeting_matches."""
    clauses, params = [], []
    if countries:
        clauses.append(f"country IN ({','.join('?' * len(countries))})")
        params += [str(c) for c in countries]
    if regions:
        clauses.append(f"region IN ({','.join('?' * len(regions))})")
        params += [str(r) for r in regions]
    if not clauses:
        return 0
    row = await (await db.execute(
        f"SELECT COUNT(DISTINCT device_id) AS n FROM telemetry_events "
        f"WHERE {' AND '.join(clauses)}", params,
    )).fetchone()
    return (row["n"] if row else 0) or 0


def _targeting_matches(targeting: dict, user: UserRecord | None, profile: dict | None) -> bool:
    """Targeting. `user` is None for the unsigned base (BYOK / on-device), the
    prime cross-promo audience — a campaign with no constraint reaches them.
    Identity dims (users/tiers/signed_in) apply only when signed in. Profile dims
    (locales / app_version / meetings_recorded / active_within_days /
    device_families) use the server-built device profile; a dim we can't verify
    (no telemetry) does not match. Absent rule = no constraint on that dimension.
    """
    p = profile or {}
    signed_in = targeting.get("signed_in")
    if signed_in is True and user is None:
        return False
    if signed_in is False and user is not None:
        return False
    users = targeting.get("users")
    if users:
        if user is None:
            return False
        identity = {user.id}
        if user.email:
            identity.add(user.email)
        if not identity.intersection(users):
            return False
    tiers = targeting.get("tiers")
    if tiers and (user is None or user.tier not in tiers):
        return False
    locales = targeting.get("locales")
    if locales and not _locale_matches(p.get("locale"), locales):
        return False
    av = targeting.get("app_version")
    if av and not _version_in_range(p.get("app_version"), av):
        return False
    mr = targeting.get("meetings_recorded")
    if mr:
        n = p.get("meetings_recorded", 0) or 0
        if mr.get("min") is not None and n < mr["min"]:
            return False
        if mr.get("max") is not None and n > mr["max"]:
            return False
    awd = targeting.get("active_within_days")
    if awd:
        la = p.get("last_active")
        if not la or not _within_days(la, awd):
            return False
    fams = targeting.get("device_families")
    if fams and not _device_family_matches(p.get("device"), fams):
        return False
    countries, regions = _geo_constraint(targeting)
    if countries and p.get("country") not in countries:
        return False
    if regions and p.get("region") not in regions:
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
    """Public: serve a promo HTML creative — the target of a variant's html_url.
    The live store wins over the bundled default, so creatives hot-reload without
    a deploy. Short cache so updates propagate fast."""
    path = promo_assets.resolve_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path, media_type="text/html",
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.get("/promo/resolve")
async def resolve_promo(
    request: Request,
    device_id: str = Query(..., min_length=1),
    user: UserRecord | None = Depends(get_current_user_optional),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Launch-ping: return the promo to show for this app/device, or {}.

    Unauthenticated — anchored on device_id so the whole install base is
    reachable (signed-in, BYOK, on-device). A bearer token is optional
    enrichment: when present it unlocks user/tier/signed_in targeting.
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
    campaigns = [_parse_campaign(r) for r in rows]
    # Build the device profile (telemetry lookup) only if some active campaign
    # targets a profile dim — keeps the common path (no profile targeting) cheap.
    needs_profile = any(_PROFILE_KEYS & set((c.get("targeting") or {}).keys()) for c in campaigns)
    profile = await _device_profile(db, device_id, user) if needs_profile else None
    for c in campaigns:
        if c.get("starts_at") and now < c["starts_at"]:
            continue
        if c.get("expires_at") and now > c["expires_at"]:
            continue
        tgt = c.get("targeting") or {}
        if not _targeting_matches(tgt, user, profile):
            continue
        # Privacy floor: a geo-targeted campaign with min_audience set is
        # withheld while its targeted geo segment is too small to be non-
        # identifying. Omitted => no floor. Only meaningful with a geo
        # constraint (without geo the segment is the whole app).
        min_aud = tgt.get("min_audience")
        if min_aud:
            countries, regions = _geo_constraint(tgt)
            if (countries or regions) and await _geo_audience(db, countries, regions) < min_aud:
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
    user: UserRecord | None = Depends(get_current_user_optional),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Client-reported promo telemetry. Writes promo_events and advances the
    per-device presentations row (frequency / cooldown / convert state).
    Unauthenticated — device_id anchors it; user_id is recorded when signed in."""
    if body.event_type not in _PROMO_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"event_type must be one of {sorted(_PROMO_EVENT_TYPES)}")
    app_id = getattr(request.state, "app_id", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO promo_events
           (id, created_at, device_id, user_id, campaign_id, variant_id, app_id,
            event_type, visible_ms, cta_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, now, body.device_id, user.id if user else None, body.campaign_id,
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
