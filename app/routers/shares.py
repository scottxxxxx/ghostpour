"""Meeting share via iMessage: the GP routes.

Authenticated (SS, bearer JWT):
  POST   /v1/shares                 archive bytes + preview fields -> {share_id, url, expires_at}
  DELETE /v1/shares/{share_id}      owner only; immediate; page goes 410
  GET    /v1/shares/{share_id}/stats owner only; view_count excludes preview fetchers

Public (the share host, no sign-in):
  GET /s/{token}                    the hosted page (renderer lands with SS's archive spec)
  GET /s/{token}/archive            the archive bytes, for the app's universal-link handler
  GET /.well-known/apple-app-site-association

A link-preview fetcher only ever meets the page route, never the archive,
because the split is by path, not by Accept. Nothing here imports
Context Quilt: the shared object is SS's meeting record, never memory.
"""
from __future__ import annotations

import json
import logging

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import meeting_shares as shares
from app.services.entitlements import entitlement_state

logger = logging.getLogger("ghostpour.shares")

router = APIRouter()
public = APIRouter()


def _meta(request: Request, key: str) -> str | None:
    v = request.headers.get(f"X-Share-{key}")
    return v if v else None


@router.post("/shares")
async def create_share(
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
    content_type: str = Header(default="application/octet-stream"),
    x_share_title: str = Header(..., alias="X-Share-Title"),
    x_share_date: str | None = Header(default=None, alias="X-Share-Date"),
    x_share_duration: int | None = Header(default=None, alias="X-Share-Duration-Seconds"),
    x_share_summary: str | None = Header(default=None, alias="X-Share-Summary-Line"),
    x_share_transcript: str | None = Header(default=None, alias="X-Share-Transcript-Included"),
    x_share_expiry: int | None = Header(default=None, alias="X-Share-Expiry-Days"),
):
    """Body is the raw `.shouldersurf` archive; the card fields ride in
    X-Share-* headers so the bytes are stored exactly as uploaded with no
    multipart parsing between SS's archive and our disk."""
    rc = request.app.state.remote_configs
    if entitlement_state(rc, user.effective_tier, "share") == "disabled":
        raise HTTPException(status_code=403, detail={"code": "share_disabled", "message": "Sharing is not available on this plan."})
    caps = shares.tier_share_caps(rc, user.effective_tier)
    transcript_included = (x_share_transcript or "").lower() in ("1", "true", "yes")
    if transcript_included and not caps["transcript_allowed"]:
        raise HTTPException(status_code=403, detail={"code": "share_transcript_disabled", "message": "Transcripts cannot be shared on this plan."})
    if await shares.creations_today(db, user.id) >= caps["creations_per_day"]:
        raise HTTPException(status_code=429, detail={"code": "share_rate_limited", "message": "Daily share limit reached."})
    archive = await request.body()
    if not archive:
        raise HTTPException(status_code=422, detail={"code": "share_empty", "message": "No archive in body."})
    settings = shares.share_settings(rc)
    cap_bytes = caps["max_archive_bytes"]
    if cap_bytes is not None and len(archive) > cap_bytes:
        # No cap by default (Scott, 2026-08-22). When a tier's dial sets
        # one, the 413 says the numbers so the client has something true
        # to show; real bundles run 275 KB to 36.9 MB with audio.
        mb = round(len(archive) / 1048576, 1); limit_mb = round(cap_bytes / 1048576, 1)
        raise HTTPException(status_code=413, detail={
            "code": "share_too_large",
            "message": f"This share is {mb} MB; the limit on this plan is {limit_mb} MB. Share it without audio, or share a shorter meeting.",
            "size_bytes": len(archive), "limit_bytes": cap_bytes})
    expiry = min(max(int(x_share_expiry or settings["default_expiry_days"]), 1), settings["max_expiry_days"])
    created = await shares.create_share(
        db, user_id=user.id, app_id=getattr(request.state, "app_id", None),
        archive=archive, media_type=content_type, title=x_share_title,
        meeting_date=x_share_date, duration_seconds=x_share_duration,
        summary_line=x_share_summary, transcript_included=transcript_included,
        expiry_days=expiry)
    return {"share_id": created["share_id"],
            "url": f"{settings['host']}/s/{created['token']}",
            "expires_at": created["expires_at"]}


@router.delete("/shares/{share_id}")
async def revoke_share(share_id: str, user: UserRecord = Depends(get_current_user),
                       db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_id(db, share_id)
    if row is None or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail={"code": "share_not_found"})
    await shares.revoke(db, share_id)
    return {"share_id": share_id, "status": "revoked"}


@router.get("/shares/{share_id}/stats")
async def share_stats(share_id: str, user: UserRecord = Depends(get_current_user),
                      db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_id(db, share_id)
    if row is None or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail={"code": "share_not_found"})
    return {"share_id": share_id, "view_count": row["view_count"], "expires_at": row["expires_at"],
            "revoked": bool(row["revoked_at"]), "live": shares.is_live(row)}


# --- public -------------------------------------------------------------------

_GONE_HTML = "<!doctype html><meta charset='utf-8'><title>Shoulder Surf</title><p>This shared meeting is no longer available.</p>"


@public.get("/.well-known/apple-app-site-association")
async def aasa(request: Request):
    ids = shares.aasa_app_ids(request.app.state.remote_configs)
    if not ids:
        raise HTTPException(status_code=404)
    body = {"applinks": {"apps": [], "details": [{"appIDs": ids, "components": [{"/": "/s/*"}]}]}}
    return Response(content=json.dumps(body), media_type="application/json")


@public.get("/s/{token}/archive")
async def share_archive(token: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_token(db, token)
    if not shares.is_live(row):
        raise HTTPException(status_code=410)
    with open(row["storage_path"], "rb") as f:
        data = f.read()
    return Response(content=data, media_type=row["media_type"],
                    headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@public.get("/s/{token}")
async def share_page(token: str, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_token(db, token)
    if not shares.is_live(row):
        return HTMLResponse(_GONE_HTML, status_code=410, headers={"X-Robots-Tag": "noindex"})
    if not shares.is_preview_fetcher(request.headers.get("User-Agent")):
        await shares.count_view(db, row["id"])
    # The page: card chrome plus the bundle's own report (SS's archive
    # spec 2026-08-22). A bundle that is not a zip, or a zip with odd
    # contents, still gets the card; the bytes are never the page.
    from app.services.share_bundle import read_bundle, render_share_page
    try:
        with open(row["storage_path"], "rb") as f:
            bundle = read_bundle(f.read())
    except Exception as e:  # noqa: BLE001  (BadZipFile, OSError, anything)
        logger.warning("share_bundle_unreadable", extra={"share_id": row["id"], "error": type(e).__name__})
        bundle = {"meetings": []}
    html = render_share_page(
        bundle, card_title=row["title"], card_desc=row["summary_line"] or "",
        transcript_included=bool(row["transcript_included"]), expires_at=row["expires_at"])
    return HTMLResponse(html, headers={"X-Robots-Tag": "noindex", "Cache-Control": "private, no-store"})


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
