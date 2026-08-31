import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.database import get_db
from app.models.user import (
    AppleAuthRequest,
    AuthResponse,
    RefreshRequest,
    UserPublic,
)
from app.services.jwt_service import JWTService

router = APIRouter()


async def _build_auth_response(
    db: aiosqlite.Connection,
    jwt_service: JWTService,
    user_id: str,
    tier: str,
    email: str | None,
    app_id: str | None = None,
    display_name: str | None = None,
) -> AuthResponse:
    """Create access + refresh tokens and return AuthResponse.

    `app_id` (the caller's X-App-ID) is stamped on the session row and
    recorded in `user_apps`. Both feed per-app account deletion: accounts
    are shared across apps because Apple's subject identifier is issued
    per developer team, so the purge needs to know which app a session
    and an account membership belong to. A missing/unknown header leaves
    the session unattributed, which the purge treats as deletable by any
    app rather than surviving a delete.
    """
    access_token = jwt_service.create_access_token(user_id)
    raw_refresh, refresh_hash, refresh_expires = jwt_service.create_refresh_token()

    now = datetime.now(timezone.utc).isoformat()
    scoped_app = app_id if app_id and app_id != "unknown" else None
    await db.execute(
        """INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at, app_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, refresh_hash, refresh_expires.isoformat(),
         now, scoped_app),
    )
    if scoped_app:
        await db.execute(
            """INSERT INTO user_apps (user_id, app_id, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, app_id) DO UPDATE SET last_seen_at = excluded.last_seen_at""",
            (user_id, scoped_app, now, now),
        )
    await db.commit()

    return AuthResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=jwt_service.access_expire.total_seconds(),
        user=UserPublic(id=user_id, tier=tier, email=email, display_name=display_name),
    )


@router.post("/apple", response_model=AuthResponse)
async def apple_auth(
    body: AppleAuthRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Exchange an Apple identity token for GhostPour access + refresh tokens."""
    apple_verifier = request.app.state.apple_verifier
    jwt_service = request.app.state.jwt_service

    try:
        claims = apple_verifier.verify_identity_token(body.identity_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Apple token: {e}")

    apple_sub = claims["sub"]
    email = claims.get("email")
    # Apple sends full_name only on first sign-in; iOS app forwards it
    display_name = body.full_name

    # Upsert user
    cursor = await db.execute(
        "SELECT * FROM users WHERE apple_sub = ?", (apple_sub,)
    )
    row = await cursor.fetchone()

    now = datetime.now(timezone.utc).isoformat()

    if row:
        user_id = row["id"]
        tier = row["tier"]
        # The stored name survives sign-ins that carry none (Apple only
        # sends fullName the first time); a fresh non-null one wins.
        stored_name = row["display_name"] if "display_name" in row.keys() else None
        if not display_name:
            display_name = stored_name
        # Update email and display_name when available (idempotent)
        updates = []
        params = []
        if email:
            updates.append("email = ?")
            params.append(email)
        if display_name:
            updates.append("display_name = ?")
            params.append(display_name)
        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(user_id)
            await db.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()
    else:
        user_id = str(uuid.uuid4())
        tier = "free"
        await db.execute(
            """INSERT INTO users (id, apple_sub, email, display_name, tier, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, apple_sub, email, display_name, tier, now, now),
        )
        await db.commit()

    return await _build_auth_response(
        db, jwt_service, user_id, tier, email,
        app_id=getattr(request.state, "app_id", None),
        display_name=display_name,
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    body: RefreshRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Exchange a refresh token for a new access + refresh token pair."""
    jwt_service = request.app.state.jwt_service

    token_hash = JWTService.hash_token(body.refresh_token)
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        """SELECT rt.*, u.tier, u.email, u.is_active, u.display_name
           FROM refresh_tokens rt
           JOIN users u ON rt.user_id = u.id
           WHERE rt.token_hash = ? AND rt.revoked = 0 AND rt.expires_at > ?""",
        (token_hash, now),
    )
    row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    # Revoke old refresh token
    await db.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
        (token_hash,),
    )
    await db.commit()

    # Prefer the live header, but inherit the rotated-out session's app_id
    # when the client sends no usable one, so a long-lived session keeps
    # its app attribution instead of decaying to unattributed on refresh.
    header_app = getattr(request.state, "app_id", None)
    if not header_app or header_app == "unknown":
        header_app = row["app_id"]

    return await _build_auth_response(
        db, jwt_service, row["user_id"], row["tier"], row["email"],
        app_id=header_app, display_name=row["display_name"],
    )
