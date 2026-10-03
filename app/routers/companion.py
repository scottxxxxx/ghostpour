"""Shoulder Surf Companion downloads (2026-09-29, agreed with SS).

  GET /v1/companion/download/{mac|windows}[?arch=x64|arm64]  -> 302

The website links here instead of straight at the bucket, so every download
is counted: platform, arch, a hashed IP, coarse country, the user agent's
family and the referrer's host. Never the raw IP, never the full user agent
or referrer URL. The targets live in the server-only `companion-downloads`
config, so a new file is a config edit, not a deploy.

A failure to record never blocks the download: the person clicked a link and
gets the file either way.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db
from app.routers.telemetry import _client_ip, _ip_hash

logger = logging.getLogger(__name__)
router = APIRouter()

_ARCHES = ("x64", "arm64")
_DOWNLOAD_RPM_PER_IP = 30


def _ua_family(ua: str) -> str:
    ua = ua.lower()
    for needle, name in (("edg/", "edge"), ("chrome/", "chrome"), ("firefox/", "firefox"),
                         ("safari/", "safari"), ("curl/", "curl"), ("wget/", "wget")):
        if needle in ua:
            return name
    return "other" if ua else "none"


def _target(cfg: dict, platform: str, arch: str | None) -> tuple[str | None, str | None]:
    if platform == "mac":
        return cfg.get("mac"), arch
    files = cfg.get("windows") or {}
    arch = arch if arch in _ARCHES else "x64"
    return files.get(arch), arch


@router.get("/companion/download/{platform}")
async def download(platform: str, request: Request, arch: str | None = None,
                   db: aiosqlite.Connection = Depends(get_db)) -> RedirectResponse:
    if platform not in ("mac", "windows"):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Unknown platform"})
    cfg = request.app.state.remote_configs.get("companion-downloads") or {}
    url, arch = _target(cfg, platform, arch)
    if not url:
        raise HTTPException(status_code=404, detail={"code": "not_found",
                                                     "message": "No download for that platform yet"})
    ip = _client_ip(request)
    ip_h = _ip_hash(ip)
    allowed = True
    if ip_h:
        allowed, _ = request.app.state.rate_limiter.check(f"companion_dl:{ip_h}", _DOWNLOAD_RPM_PER_IP)
    if allowed:  # a flood is still served the file, just not counted
        try:
            from app.services import geoip
            await db.execute(
                """INSERT INTO companion_downloads
                   (id, platform, arch, ip_hash, country, ua_family, referrer_host, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), platform, arch, ip_h, (geoip.lookup(ip) or {}).get("country"),
                 _ua_family(request.headers.get("user-agent", "")),
                 urlparse(request.headers.get("referer", "")).hostname,
                 datetime.now(timezone.utc).isoformat()))
            await db.commit()
        except Exception:
            logger.exception("companion download: record failed (file still served)")
    return RedirectResponse(url, status_code=302)
