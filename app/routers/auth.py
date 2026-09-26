import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.database import get_db
from app.models.user import (
    ANONYMOUS_SUB_PREFIX,
    AnonymousAuthRequest,
    AppleAuthRequest,
    AuthResponse,
    RefreshRequest,
    UserPublic,
)
from app.routers.telemetry import _client_ip, _ip_hash, _UUID_RE
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
    is_anonymous: bool = False,
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
        user=UserPublic(id=user_id, tier=tier, email=email, display_name=display_name,
                        is_anonymous=is_anonymous),
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


# Anonymous purchase accounts (2026-09-26). Apple rejected iOS 1.18 three times
# under 5.1.1(v): buying a plan may not require registration, and the inline
# Sign in with Apple counted as registration. A signed-out plan tap now gets a
# silent account holding no personal data, the purchase binds to it through
# appAccountToken, and Sign in with Apple stays optional. ShoulderSurf only:
# it is the app Apple rejected, and no other app has been designed for this.
ANONYMOUS_APPS = {"shouldersurf"}
# Per client IP, per minute. The client calls this at purchase time only.
_ANONYMOUS_RPM_PER_IP = 5


def anonymous_sub(install_id: str) -> str:
    """The `apple_sub` of the anonymous account for this install. Lowercased
    first: Swift's uuidString is uppercase and must not mint a second account."""
    return ANONYMOUS_SUB_PREFIX + hashlib.sha256(install_id.strip().lower().encode()).hexdigest()


@router.post("/anonymous", response_model=AuthResponse)
async def anonymous_auth(
    body: AnonymousAuthRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Tokens for this install's anonymous account, creating it on first call.

    Idempotent per install id. The account has the free tier's NAME but no
    allowance until a verified purchase binds a plan (app/services/allowance.py).
    An account closed by a merge into an Apple account answers 410, so the
    client offers Sign in with Apple instead of silently making a new one.
    """
    app_id = getattr(request.state, "app_id", None)
    if app_id not in ANONYMOUS_APPS:
        raise HTTPException(status_code=403, detail={
            "code": "anonymous_not_offered",
            "message": "Anonymous accounts are not offered for this app."})
    if not _UUID_RE.match(body.install_id.strip()):
        raise HTTPException(status_code=400, detail={
            "code": "invalid_request", "message": "install_id must be a UUID"})

    ip_h = _ip_hash(_client_ip(request))
    if ip_h:
        allowed, retry_after = request.app.state.rate_limiter.check(
            f"anonymous:{ip_h}", _ANONYMOUS_RPM_PER_IP)
        if not allowed:
            raise HTTPException(status_code=429, detail={
                "code": "rate_limited",
                "message": f"Too many requests; retry in {retry_after}s",
                "details": {"retry_after": retry_after}})

    sub = anonymous_sub(body.install_id)
    row = await (await db.execute(
        "SELECT id, tier, is_active FROM users WHERE apple_sub = ?", (sub,))).fetchone()
    if row is None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            await db.execute(
                """INSERT INTO users (id, apple_sub, email, display_name, tier, created_at, updated_at)
                   VALUES (?, ?, NULL, NULL, 'free', ?, ?)""",
                (str(uuid.uuid4()), sub, now, now))
            await db.commit()
        except sqlite3.IntegrityError:
            # A concurrent first call for the same install won the insert.
            await db.rollback()
        row = await (await db.execute(
            "SELECT id, tier, is_active FROM users WHERE apple_sub = ?", (sub,))).fetchone()
    if not row["is_active"]:
        raise HTTPException(status_code=410, detail={
            "code": "anonymous_account_closed",
            "message": "This purchase moved to your Apple account. Sign in with Apple."})

    return await _build_auth_response(
        db, request.app.state.jwt_service, row["id"], row["tier"], None,
        app_id=app_id, is_anonymous=True)


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
        """SELECT rt.*, u.tier, u.email, u.is_active, u.display_name, u.apple_sub
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
        is_anonymous=str(row["apple_sub"]).startswith(ANONYMOUS_SUB_PREFIX),
    )
