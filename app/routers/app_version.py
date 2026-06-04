"""Per-app version metadata endpoint.

`GET /v1/app/version` with header `X-App-Bundle-Id: <bundle id>` returns
the version block for that app. No auth: the call fires on launch
before sign-in. Trust comes from the request being explicit about who
is asking (bundle id baked into the iOS binary); an attacker poking
this endpoint just gets back the same public version data anyone with
TestFlight can see.

Multi-tenant from day one: missing bundle id is 400 (request shape
problem), unknown bundle id is 404 (this gateway doesn't know that
app), known bundle id with no platforms block is also 404 (entry exists
but is empty, surface the misconfig). 200 only on a real hit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.services.app_version import get_version_info

logger = logging.getLogger("ghostpour.app_version")

router = APIRouter()


@router.get("/app/version")
async def get_app_version(
    request: Request,
    response: Response,
    x_app_bundle_id: str | None = Header(default=None),
):
    if not x_app_bundle_id or not x_app_bundle_id.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "missing_bundle_id",
                "message": "X-App-Bundle-Id header is required.",
            },
        )
    bundle_id = x_app_bundle_id.strip()
    registry = getattr(request.app.state, "app_versions", {}) or {}
    info = get_version_info(registry, bundle_id)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "unknown_bundle_id",
                "message": f"No version metadata for bundle id {bundle_id!r}.",
            },
        )
    response.headers["Cache-Control"] = "public, max-age=300"
    return info
