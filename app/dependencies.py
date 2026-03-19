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
        tier=row["tier"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_active=bool(row["is_active"]),
    )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    return user
