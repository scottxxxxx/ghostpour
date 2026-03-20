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
) -> AuthResponse:
    """Create access + refresh tokens and return AuthResponse."""
    access_token = jwt_service.create_access_token(user_id)
    raw_refresh, refresh_hash, refresh_expires = jwt_service.create_refresh_token()

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, refresh_hash, refresh_expires.isoformat(), now),
    )
    await db.commit()

    return AuthResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=jwt_service.access_expire.total_seconds(),
        user=UserPublic(id=user_id, tier=tier, email=email),
    )


@router.post("/apple", response_model=AuthResponse)
async def apple_auth(
    body: AppleAuthRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Exchange an Apple identity token for CloudZap access + refresh tokens."""
    apple_verifier = request.app.state.apple_verifier
    jwt_service = request.app.state.jwt_service

    try:
        claims = apple_verifier.verify_identity_token(body.identity_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Apple token: {e}")

    apple_sub = claims["sub"]
    email = claims.get("email")

    # Upsert user
    cursor = await db.execute(
        "SELECT * FROM users WHERE apple_sub = ?", (apple_sub,)
    )
    row = await cursor.fetchone()

    now = datetime.now(timezone.utc).isoformat()

    if row:
        user_id = row["id"]
        tier = row["tier"]
        if email:
            await db.execute(
                "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
                (email, now, user_id),
            )
            await db.commit()
    else:
        user_id = str(uuid.uuid4())
        tier = "free"
        await db.execute(
            """INSERT INTO users (id, apple_sub, email, tier, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, apple_sub, email, tier, now, now),
        )
        await db.commit()

    return await _build_auth_response(db, jwt_service, user_id, tier, email)


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
        """SELECT rt.*, u.tier, u.email, u.is_active
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

    return await _build_auth_response(
        db, jwt_service, row["user_id"], row["tier"], row["email"]
    )
