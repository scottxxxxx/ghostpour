import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest, ChatResponse
from app.models.user import UserRecord

router = APIRouter()


@router.get("/usage/me")
async def usage_me(
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get the authenticated user's current allocation, overage balance, and usage."""
    tier_config = request.app.state.tier_config
    tier = tier_config.tiers.get(user.tier)

    monthly_limit = tier.monthly_cost_limit_usd if tier else -1

    # Read allocation state
    cursor = await db.execute(
        """SELECT monthly_used_usd, overage_balance_usd, allocation_resets_at
           FROM users WHERE id = ?""",
        (user.id,),
    )
    row = await cursor.fetchone()
    monthly_used = float(row["monthly_used_usd"] or 0)
    overage_balance = float(row["overage_balance_usd"] or 0)
    resets_at = row["allocation_resets_at"]

    # This month's usage stats
    cursor = await db.execute(
        """SELECT
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(cached_tokens), 0) as cached_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', 'start of month')
             AND status = 'success'""",
        (user.id,),
    )
    stats = await cursor.fetchone()

    # Convert cost to hours for user-friendly display
    model_cost_per_hour = 0.05 if "haiku" in (tier.default_model or "") else 0.19
    hours_used = monthly_used / model_cost_per_hour if model_cost_per_hour > 0 else 0
    hours_limit = monthly_limit / model_cost_per_hour if monthly_limit > 0 else -1
    overage_hours = overage_balance / model_cost_per_hour if model_cost_per_hour > 0 else 0

    return {
        "tier": user.tier,
        "tier_display_name": tier.display_name if tier else user.tier,
        "allocation": {
            "monthly_limit_usd": monthly_limit,
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_remaining_usd": round(max(0, monthly_limit - monthly_used), 4) if monthly_limit != -1 else -1,
            "percent_used": round(monthly_used / monthly_limit * 100, 1) if monthly_limit > 0 else 0,
            "resets_at": resets_at,
        },
        "hours": {
            "used": round(hours_used, 1),
            "limit": round(hours_limit, 1) if hours_limit != -1 else -1,
            "remaining": round(max(0, hours_limit - hours_used), 1) if hours_limit != -1 else -1,
        },
        "overage": {
            "balance_usd": round(overage_balance, 4),
            "balance_hours": round(overage_hours, 1),
        },
        "this_month": {
            "requests": stats["requests"],
            "input_tokens": stats["input_tokens"],
            "output_tokens": stats["output_tokens"],
            "cached_tokens": stats["cached_tokens"],
            "cost_usd": round(stats["cost"], 4),
        },
        "summary_mode": tier.summary_mode if tier else "delta",
        "summary_interval_minutes": tier.summary_interval_minutes if tier else 10,
        "max_images_per_request": tier.max_images_per_request if tier else 0,
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy an LLM request through CloudZap with auth, tier, and rate enforcement."""
    tier_config = request.app.state.tier_config
    provider_router = request.app.state.provider_router
    rate_limiter = request.app.state.rate_limiter
    usage_tracker = request.app.state.usage_tracker
    pricing = request.app.state.pricing

    # 1. Look up tier
    tier = tier_config.tiers.get(user.tier)
    if not tier:
        raise HTTPException(
            status_code=500,
            detail={"code": "invalid_request", "message": f"Unknown tier: {user.tier}"},
        )

    # 2. Resolve "auto" model to tier's default
    if body.model == "auto" or body.provider == "auto":
        if not tier.default_model:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_request",
                    "message": "No default model configured for this tier",
                },
            )
        parts = tier.default_model.split("/", 1)
        if len(parts) == 2:
            body = body.model_copy(update={"provider": parts[0], "model": parts[1]})
        else:
            body = body.model_copy(update={"model": tier.default_model})

    # 3. Check provider + model access
    usage_tracker.check_model_access(body, tier)

    # 4. Rate limit
    allowed, retry_after = rate_limiter.check(user.id, tier.requests_per_minute)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                "details": {"retry_after": retry_after},
            },
        )

    # 5. Monthly allocation + overage check
    monthly_used, overage_balance = await usage_tracker.check_quota(db, user, tier)

    # 6. Route to provider
    start = time.monotonic()
    try:
        response = await provider_router.route(body)
    except HTTPException:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        await usage_tracker.log_usage(
            db, user.id, body, None, elapsed_ms, status="error"
        )
        raise

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # 7. Calculate cost from pricing data
    request_cost = 0.0
    if pricing.is_loaded:
        cost = pricing.calculate_cost(
            provider=body.provider,
            model=body.model,
            usage=response.usage,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        response.cost = cost
        request_cost = cost.get("total_cost", 0.0)

    # 8. Record cost against allocation/overage
    await usage_tracker.record_cost(db, user.id, request_cost, tier)

    # 9. Log usage
    await usage_tracker.log_usage(db, user.id, body, response, elapsed_ms)

    # 10. Build response with allocation headers
    response_data = response.model_dump()
    json_response = JSONResponse(content=response_data)

    if tier.monthly_cost_limit_usd != -1:
        new_monthly_used = monthly_used + request_cost
        percent = min(100, new_monthly_used / tier.monthly_cost_limit_usd * 100)
        json_response.headers["X-Allocation-Percent"] = f"{percent:.1f}"
        if percent >= 80:
            json_response.headers["X-Allocation-Warning"] = "true"
        json_response.headers["X-Monthly-Used"] = f"{new_monthly_used:.4f}"
        json_response.headers["X-Monthly-Limit"] = f"{tier.monthly_cost_limit_usd:.2f}"
        json_response.headers["X-Overage-Balance"] = f"{overage_balance:.4f}"

    return json_response
