from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.database import get_db

router = APIRouter()


def _verify_admin(request: Request, x_admin_key: str) -> None:
    settings = request.app.state.settings
    if not settings.admin_key or x_admin_key != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


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
    """Set a user's subscription tier with dollar-value carryover on upgrade.

    On upgrade: unused allocation from the old tier is converted to dollar
    value and added to the new tier's allocation. monthly_used_usd resets to 0.

    On downgrade: allocation resets to the new tier's limit. No carryover
    (downgrades take effect at period end in production via StoreKit).
    """
    _verify_admin(request, x_admin_key)

    tier_config = request.app.state.tier_config
    if body.tier not in tier_config.tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {body.tier}. Available: {list(tier_config.tiers.keys())}",
        )

    new_tier = tier_config.tiers[body.tier]

    # Read current user state
    cursor = await db.execute(
        "SELECT tier, monthly_used_usd, monthly_cost_limit_usd, overage_balance_usd FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    old_tier_name = row["tier"]
    old_used = float(row["monthly_used_usd"] or 0)
    old_limit = float(row["monthly_cost_limit_usd"] or 0)
    overage = float(row["overage_balance_usd"] or 0)

    # Calculate carryover
    carryover = 0.0
    carryover_detail = "none"

    old_tier = tier_config.tiers.get(old_tier_name)
    if old_tier and old_limit > 0 and new_tier.monthly_cost_limit_usd > old_limit:
        # Upgrade: carry forward unused dollar value
        unused = max(0, old_limit - old_used)
        carryover = unused
        carryover_detail = f"${unused:.4f} unused from {old_tier.display_name}"

    # Apply tier change
    now = datetime.now(timezone.utc)
    new_limit = new_tier.monthly_cost_limit_usd
    # Reset period: 30 days from now
    resets_at = (now + timedelta(days=30)).isoformat()

    # New monthly_used starts at 0, but carryover effectively increases the limit
    # We model this by adding carryover to overage balance (simplest, same economic effect)
    new_overage = overage + carryover

    await db.execute(
        """UPDATE users SET
            tier = ?,
            monthly_cost_limit_usd = ?,
            monthly_used_usd = 0,
            overage_balance_usd = ?,
            allocation_resets_at = ?,
            updated_at = ?
           WHERE id = ?""",
        (body.tier, new_limit, new_overage, resets_at, now.isoformat(), body.user_id),
    )
    await db.commit()

    return {
        "status": "ok",
        "user_id": body.user_id,
        "old_tier": old_tier_name,
        "new_tier": body.tier,
        "monthly_limit_usd": new_limit,
        "overage_balance_usd": round(new_overage, 4),
        "carryover": carryover_detail,
        "allocation_resets_at": resets_at,
    }


@router.get("/admin/dashboard")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
):
    """Admin dashboard: users, usage, costs, latency. Protected by admin key."""
    _verify_admin(request, x_admin_key)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Users ---
    cursor = await db.execute("SELECT COUNT(*) FROM users")
    total_users = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
    active_users = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT tier, COUNT(*) FROM users WHERE is_active = 1 GROUP BY tier"
    )
    tier_breakdown = {row[0]: row[1] for row in await cursor.fetchall()}

    # --- Usage (last N days) ---
    since = f"{days}d"
    cursor = await db.execute(
        """SELECT
            COUNT(*) as total_requests,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
            SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END) as rate_limited,
            COALESCE(SUM(input_tokens), 0) as total_input_tokens,
            COALESCE(SUM(output_tokens), 0) as total_output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as total_cost_usd,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms,
            MAX(response_time_ms) as max_latency_ms,
            MIN(response_time_ms) as min_latency_ms
           FROM usage_log
           WHERE request_timestamp >= date('now', ?)""",
        (f"-{days} days",),
    )
    row = await cursor.fetchone()
    usage_summary = {
        "period_days": days,
        "total_requests": row[0],
        "successful": row[1],
        "errors": row[2],
        "rate_limited": row[3],
        "total_input_tokens": row[4],
        "total_output_tokens": row[5],
        "total_tokens": row[4] + row[5],
        "total_cost_usd": round(row[6], 4),
        "avg_latency_ms": int(row[7]) if row[7] else 0,
        "max_latency_ms": row[8],
        "min_latency_ms": row[9],
    }

    # --- Usage by provider ---
    cursor = await db.execute(
        """SELECT provider, model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'
           GROUP BY provider, model
           ORDER BY requests DESC""",
        (f"-{days} days",),
    )
    by_model = [
        {
            "provider": r[0],
            "model": r[1],
            "requests": r[2],
            "input_tokens": r[3],
            "output_tokens": r[4],
            "cost_usd": round(r[5], 4),
            "avg_latency_ms": int(r[6]) if r[6] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # --- Usage by user (top 10) ---
    cursor = await db.execute(
        """SELECT u.id, u.email, u.tier,
            COUNT(*) as requests,
            COALESCE(SUM(l.input_tokens), 0) + COALESCE(SUM(l.output_tokens), 0) as total_tokens,
            COALESCE(SUM(l.estimated_cost_usd), 0) as cost_usd,
            MAX(l.request_timestamp) as last_request
           FROM usage_log l
           JOIN users u ON l.user_id = u.id
           WHERE l.request_timestamp >= date('now', ?) AND l.status = 'success'
           GROUP BY u.id
           ORDER BY total_tokens DESC
           LIMIT 10""",
        (f"-{days} days",),
    )
    top_users = [
        {
            "user_id": r[0],
            "email": r[1],
            "tier": r[2],
            "requests": r[3],
            "total_tokens": r[4],
            "cost_usd": round(r[5], 4),
            "last_request": r[6],
        }
        for r in await cursor.fetchall()
    ]

    # --- Today's usage ---
    cursor = await db.execute(
        """SELECT
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd
           FROM usage_log
           WHERE request_timestamp >= ? AND status = 'success'""",
        (today,),
    )
    today_row = await cursor.fetchone()
    today_usage = {
        "requests": today_row[0],
        "tokens": today_row[1],
        "cost_usd": round(today_row[2], 4),
    }

    # --- Latency percentiles (last N days) ---
    cursor = await db.execute(
        """SELECT response_time_ms FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'
           ORDER BY response_time_ms""",
        (f"-{days} days",),
    )
    latencies = [r[0] for r in await cursor.fetchall() if r[0] is not None]
    percentiles = {}
    if latencies:
        for p in [50, 75, 90, 95, 99]:
            idx = int(len(latencies) * p / 100)
            percentiles[f"p{p}"] = latencies[min(idx, len(latencies) - 1)]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users": {
            "total": total_users,
            "active": active_users,
            "by_tier": tier_breakdown,
        },
        "today": today_usage,
        "usage": usage_summary,
        "by_model": by_model,
        "top_users": top_users,
        "latency_percentiles": percentiles,
    }


@router.get("/admin/tiers")
async def get_tiers(
    request: Request,
    x_admin_key: str = Header(...),
):
    """View all tier configurations with their model/provider access rules."""
    _verify_admin(request, x_admin_key)
    tier_config = request.app.state.tier_config

    tiers = {}
    for name, tier in tier_config.tiers.items():
        tiers[name] = {
            "display_name": tier.display_name,
            "default_model": tier.default_model,
            "monthly_cost_limit_usd": tier.monthly_cost_limit_usd,
            "requests_per_minute": tier.requests_per_minute,
            "summary_mode": tier.summary_mode,
            "summary_interval_minutes": tier.summary_interval_minutes,
            "allowed_providers": tier.allowed_providers,
            "allowed_models": tier.allowed_models,
            "max_images_per_request": tier.max_images_per_request,
            "storekit_product_id": tier.storekit_product_id,
        }

    return {"tiers": tiers}


@router.get("/admin/user/{user_id}")
async def user_detail(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=30, ge=1, le=90),
):
    """Detailed user view with budget, usage breakdown by call type, and query history."""
    _verify_admin(request, x_admin_key)
    tier_config = request.app.state.tier_config

    # User info
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user_tier = row["tier"]
    tier = tier_config.tiers.get(user_tier)

    # Monthly budget
    cursor = await db.execute(
        """SELECT
            COALESCE(SUM(COALESCE(input_tokens, 0)), 0) as input_tokens,
            COALESCE(SUM(COALESCE(output_tokens, 0)), 0) as output_tokens,
            COALESCE(SUM(COALESCE(cached_tokens, 0)), 0) as cached_tokens,
            COALESCE(SUM(COALESCE(estimated_cost_usd, 0)), 0) as total_cost,
            COUNT(*) as total_requests
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', 'start of month')
             AND status = 'success'""",
        (user_id,),
    )
    month_row = await cursor.fetchone()

    monthly_limit = tier.daily_cost_limit_usd * 30 if tier and tier.daily_cost_limit_usd != -1 else -1
    monthly_used = month_row["total_cost"]

    # Usage by call type
    cursor = await db.execute(
        """SELECT
            call_type,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(cached_tokens), 0) as cached_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms,
            COALESCE(SUM(image_count), 0) as total_images
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY call_type
           ORDER BY requests DESC""",
        (user_id, f"-{days} days"),
    )
    by_call_type = [
        {
            "call_type": r["call_type"] or "unknown",
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cached_tokens": r["cached_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
            "total_images": r["total_images"],
        }
        for r in await cursor.fetchall()
    ]

    # Usage by prompt mode
    cursor = await db.execute(
        """SELECT
            prompt_mode,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as total_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY prompt_mode
           ORDER BY requests DESC""",
        (user_id, f"-{days} days"),
    )
    by_prompt_mode = [
        {
            "prompt_mode": r["prompt_mode"] or "unknown",
            "requests": r["requests"],
            "total_tokens": r["total_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Usage by model
    cursor = await db.execute(
        """SELECT
            provider, model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY provider, model
           ORDER BY requests DESC""",
        (user_id, f"-{days} days"),
    )
    by_model = [
        {
            "provider": r["provider"],
            "model": r["model"],
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Daily usage trend (last N days)
    cursor = await db.execute(
        """SELECT
            date(request_timestamp) as day,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY date(request_timestamp)
           ORDER BY day""",
        (user_id, f"-{days} days"),
    )
    daily_trend = [
        {"day": r["day"], "requests": r["requests"], "tokens": r["tokens"], "cost": round(r["cost"], 4)}
        for r in await cursor.fetchall()
    ]

    return {
        "user": {
            "id": row["id"],
            "email": row["email"],
            "tier": user_tier,
            "created_at": row["created_at"],
            "is_active": bool(row["is_active"]),
        },
        "budget": {
            "tier": user_tier,
            "monthly_limit_usd": round(monthly_limit, 2) if monthly_limit != -1 else -1,
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_remaining_usd": round(monthly_limit - monthly_used, 4) if monthly_limit != -1 else -1,
            "percent_used": round(monthly_used / monthly_limit * 100, 1) if monthly_limit > 0 else 0,
            "this_month": {
                "requests": month_row["total_requests"],
                "input_tokens": month_row["input_tokens"],
                "output_tokens": month_row["output_tokens"],
                "cached_tokens": month_row["cached_tokens"],
            },
        },
        "by_call_type": by_call_type,
        "by_prompt_mode": by_prompt_mode,
        "by_model": by_model,
        "daily_trend": daily_trend,
    }


@router.get("/admin/users")
async def list_users(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """List all users with their usage stats."""
    _verify_admin(request, x_admin_key)

    cursor = await db.execute(
        """SELECT u.id, u.apple_sub, u.email, u.tier, u.created_at, u.is_active,
            (SELECT COUNT(*) FROM usage_log l WHERE l.user_id = u.id AND l.status = 'success') as total_requests,
            (SELECT COALESCE(SUM(COALESCE(l2.input_tokens,0)) + SUM(COALESCE(l2.output_tokens,0)), 0)
             FROM usage_log l2 WHERE l2.user_id = u.id AND l2.status = 'success') as total_tokens,
            (SELECT COALESCE(SUM(l3.estimated_cost_usd), 0)
             FROM usage_log l3 WHERE l3.user_id = u.id AND l3.status = 'success') as total_cost_usd,
            (SELECT MAX(l4.request_timestamp) FROM usage_log l4 WHERE l4.user_id = u.id) as last_request
           FROM users u
           ORDER BY u.created_at DESC"""
    )
    users = [
        {
            "id": r[0],
            "apple_sub": r[1][:8] + "..." if r[1] else None,
            "email": r[2],
            "tier": r[3],
            "created_at": r[4],
            "is_active": bool(r[5]),
            "total_requests": r[6],
            "total_tokens": r[7],
            "total_cost_usd": round(r[8], 4) if r[8] else 0,
            "last_request": r[9],
        }
        for r in await cursor.fetchall()
    ]

    return {"users": users, "count": len(users)}
