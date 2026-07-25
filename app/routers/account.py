"""Account deletion endpoint (App Review 5.1.1(v), SS ask 2026-07-25).

POST /v1/account/delete
  Authorization: Bearer <JWT>
  body: {"apple_authorization_code": "<optional>"}

200 on success (client ignores the body), 401 on a bad or expired JWT.
Idempotent: a valid JWT whose user row is already gone returns 200, so
a client retry after a mid-flight failure never shows an error. That is
why this endpoint decodes the JWT itself instead of using
`get_current_user` (which 401s on a missing row).

The Apple authorization code is optional by design: when the on-device
re-auth sheet fails, the user can still delete with the JWT alone; we
purge the data and skip Sign in with Apple token revocation.
"""

import logging

import aiosqlite
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.database import get_db
from app.services import siwa_revocation
from app.services.account_deletion import delete_user_data

logger = logging.getLogger(__name__)
router = APIRouter()
_bearer = HTTPBearer()


class AccountDeleteRequest(BaseModel):
    apple_authorization_code: str | None = None


@router.post("/account/delete")
async def delete_account(
    request: Request,
    body: AccountDeleteRequest | None = None,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: aiosqlite.Connection = Depends(get_db),
):
    jwt_service = request.app.state.jwt_service
    try:
        payload = jwt_service.verify_access_token(credentials.credentials)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user_id = payload["sub"]

    cursor = await db.execute(
        "SELECT tier FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if row is None:
        # Idempotent retry: the data is already gone, which is the state
        # the caller asked for.
        return {"status": "deleted"}
    old_tier = row["tier"]

    # Dry-run window (SS ask 2026-07-25, App Review video recording):
    # scoped to SS-identified/headerless requests only — any other app
    # identity keeps the real path. Auto-expires; malformed timestamp
    # fails safe to real deletion.
    from app.config import get_settings
    _dry_until_raw = get_settings().account_delete_dryrun_until
    if _dry_until_raw:
        from datetime import datetime, timezone
        try:
            _dry_until = datetime.fromisoformat(_dry_until_raw)
        except ValueError:
            logger.error("account_delete: malformed dryrun_until %r — "
                         "treating as INACTIVE, real deletion proceeds",
                         _dry_until_raw)
            _dry_until = None
        _app = getattr(request.state, "app_id", "unknown")
        if (_dry_until and datetime.now(timezone.utc) < _dry_until
                and _app in ("shouldersurf", "unknown", None)):
            logger.warning(
                "account_delete DRYRUN: 200 without purge for user %s "
                "(app=%s, window ends %s)", user_id[:8], _app, _dry_until_raw)
            return {"status": "deleted"}

    await delete_user_data(db, user_id)

    # CQ purge trigger: the account_deleted event on the tier-change
    # endpoint (wire-contracts/cq-tier-signals.md). CQ owns its own
    # retention, so the signal is the deletion request for everything
    # CQ holds. Fire-and-forget like every other tier signal.
    from app.services.context_quilt import notify_tier_change
    try:
        await notify_tier_change(
            user_id, old_tier=old_tier, new_tier="deleted",
            event_type="account_deleted")
    except Exception:
        logger.exception("account_delete: cq notify failed")

    code = body.apple_authorization_code if body else None
    if code and siwa_revocation.is_configured():
        await siwa_revocation.revoke_with_authorization_code(code)
    elif code:
        logger.warning(
            "account_delete: authorization code present but SIWA key "
            "unconfigured — revocation skipped")

    return {"status": "deleted"}
