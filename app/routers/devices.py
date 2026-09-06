"""Device registration for push (SS contract 2026-09-05).

POST and DELETE only; the token is the caller's to give and to take away.
Both are idempotent and owner-scoped, and both work while APNs itself is
dormant, so the day the key is provisioned every phone is already known.
"""

import aiosqlite
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import device_tokens

router = APIRouter()


@router.post("/devices/apns")
async def register_device(
    request: Request,
    body: dict = Body(...),
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    token = (body.get("device_token") or "").strip()
    bundle_id = (body.get("bundle_id") or "").strip()
    environment = (body.get("environment") or "production").strip().lower()
    if not token or not bundle_id:
        raise HTTPException(status_code=400, detail="device_token and bundle_id are required")
    if environment not in device_tokens.ENVIRONMENTS:
        raise HTTPException(status_code=400, detail="environment must be sandbox or production")
    await device_tokens.register(
        db, user_id=user.id, device_token=token, environment=environment,
        bundle_id=bundle_id, app_build=(body.get("app_build") or None),
        app_id=getattr(request.state, "app_id", None),
    )
    return JSONResponse({"registered": True}, headers={"Cache-Control": "private, no-store"})


@router.delete("/devices/apns")
async def unregister_device(
    body: dict = Body(...),
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """200 whether or not it existed: sign-out is not a lookup."""
    token = (body.get("device_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="device_token is required")
    await device_tokens.unregister(db, user_id=user.id, device_token=token)
    return JSONResponse({"registered": False}, headers={"Cache-Control": "private, no-store"})
