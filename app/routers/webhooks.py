from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.database import get_db

router = APIRouter()


class SetTierRequest(BaseModel):
    user_id: str
    tier: str


@router.post("/admin/set-tier")
async def set_tier(
    body: SetTierRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Manually set a user's subscription tier. Protected by admin key."""
    settings = request.app.state.settings
    if not settings.admin_key or x_admin_key != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    # Validate tier exists
    tier_config = request.app.state.tier_config
    if body.tier not in tier_config.tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {body.tier}. Available: {list(tier_config.tiers.keys())}",
        )

    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "UPDATE users SET tier = ?, updated_at = ? WHERE id = ?",
        (body.tier, now, body.user_id),
    )
    await db.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "ok", "user_id": body.user_id, "tier": body.tier}
