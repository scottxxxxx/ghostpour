"""Per-user preferences endpoints.

Currently scoped to the marketing-opt-in flag. As more user-controlled
preferences land (digest cadence, locale override, …) they belong here
rather than scattering across routers.
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import marketing_opt_in as marketing

router = APIRouter()


class MarketingOptInRequest(BaseModel):
    opt_in: bool


@router.get("/preferences/me")
async def get_preferences(
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    state = await marketing.get_marketing_opt_in(db, user.id)
    return {
        "user_id": user.id,
        "marketing_opt_in": {
            "enabled": state["opt_in"],
            "updated_at": state["updated_at"],
            "source": state["source"],
        },
    }


@router.put("/preferences/marketing-opt-in")
async def update_marketing_opt_in(
    body: MarketingOptInRequest,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Set the user's marketing-email opt-in state.

    Source is recorded as `ios_toggle` — this endpoint is the iOS
    Settings toggle's destination. Email-side unsubscribes go through
    `/unsubscribe?token=...` and record source `unsubscribe_link`.
    Spam complaints from Resend record `spam_complaint`.
    """
    changed = await marketing.set_marketing_opt_in(
        db, user.id, opt_in=body.opt_in, source=marketing.SOURCE_IOS,
    )
    state = await marketing.get_marketing_opt_in(db, user.id)
    return {
        "user_id": user.id,
        "marketing_opt_in": {
            "enabled": state["opt_in"],
            "updated_at": state["updated_at"],
            "source": state["source"],
        },
        "changed": changed,
    }
