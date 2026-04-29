from collections.abc import AsyncGenerator

import aiosqlite
import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import get_db
from app.models.user import UserRecord

bearer_scheme = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: aiosqlite.Connection = Depends(get_db),
) -> UserRecord:
    """Verify JWT access token and return the user record."""
    jwt_service = request.app.state.jwt_service

    try:
        payload = jwt_service.verify_access_token(credentials.credentials)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    cursor = await db.execute(
        "SELECT * FROM users WHERE id = ?", (payload["sub"],)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")

    user = UserRecord(
        id=row["id"],
        apple_sub=row["apple_sub"],
        email=row["email"],
        display_name=row["display_name"],
        tier=row["tier"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_active=bool(row["is_active"]),
        monthly_cost_limit_usd=row["monthly_cost_limit_usd"],
        monthly_used_usd=float(row["monthly_used_usd"] or 0),
        overage_balance_usd=float(row["overage_balance_usd"] or 0),
        allocation_resets_at=row["allocation_resets_at"],
        simulated_tier=row["simulated_tier"],
        simulated_exhausted=bool(row["simulated_exhausted"]),
        is_trial=bool(row["is_trial"]),
        trial_start=row["trial_start"],
        trial_end=row["trial_end"],
        project_chat_used_this_period=int(row["project_chat_used_this_period"] or 0),
        project_chat_period=row["project_chat_period"],
    )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    return user


_optional_bearer = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: aiosqlite.Connection = Depends(get_db),
) -> UserRecord | None:
    """Return the user if a valid JWT is present, otherwise None.

    Used by endpoints that have different behavior for authenticated vs.
    unauthenticated callers (e.g. Project Chat preflight). Never raises
    on missing/invalid credentials — just returns None.
    """
    if credentials is None:
        return None

    jwt_service = request.app.state.jwt_service
    try:
        payload = jwt_service.verify_access_token(credentials.credentials)
    except pyjwt.PyJWTError:
        return None

    if payload.get("type") != "access":
        return None

    cursor = await db.execute(
        "SELECT * FROM users WHERE id = ?", (payload["sub"],)
    )
    row = await cursor.fetchone()
    if not row:
        return None

    user = UserRecord(
        id=row["id"],
        apple_sub=row["apple_sub"],
        email=row["email"],
        display_name=row["display_name"],
        tier=row["tier"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_active=bool(row["is_active"]),
        monthly_cost_limit_usd=row["monthly_cost_limit_usd"],
        monthly_used_usd=float(row["monthly_used_usd"] or 0),
        overage_balance_usd=float(row["overage_balance_usd"] or 0),
        allocation_resets_at=row["allocation_resets_at"],
        simulated_tier=row["simulated_tier"],
        simulated_exhausted=bool(row["simulated_exhausted"]),
        is_trial=bool(row["is_trial"]),
        trial_start=row["trial_start"],
        trial_end=row["trial_end"],
        project_chat_used_this_period=int(row["project_chat_used_this_period"] or 0),
        project_chat_period=row["project_chat_period"],
    )

    if not user.is_active:
        return None

    return user
