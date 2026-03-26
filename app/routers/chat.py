import asyncio
import time
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest, ChatResponse
from app.models.user import UserRecord
from app.services import context_quilt as cq

router = APIRouter()


# MARK: - StoreKit Receipt Verification


class VerifyReceiptRequest(BaseModel):
    product_id: str              # e.g., "com.weirtech.shouldersurf.sub.ultra.monthly"
    transaction_id: str          # StoreKit 2 original transaction ID
    signed_transaction: str | None = None  # JWS for future server-side verification
    offer_type: str | None = None  # "introductory" for free trial, None for paid
    offer_price: float | None = None  # 0.00 for free trial


# Map StoreKit product IDs to tier names
PRODUCT_TO_TIER: dict[str, str] = {}  # Populated from tier config at startup


@router.post("/verify-receipt")
async def verify_receipt(
    body: VerifyReceiptRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Verify a StoreKit 2 transaction and upgrade the user's tier.

    Called by the iOS app after a successful purchase or on app launch
    when checking currentEntitlements.

    For MVP: trusts the product_id from the authenticated client.
    StoreKit 2 transactions are cryptographically signed by Apple —
    the client has already verified them. Full server-side JWS
    verification can be added in v0.3.
    """
    tier_config = request.app.state.tier_config

    # Build product-to-tier map from config (lazy init)
    global PRODUCT_TO_TIER
    if not PRODUCT_TO_TIER:
        for name, tier in tier_config.tiers.items():
            if tier.storekit_product_id:
                PRODUCT_TO_TIER[tier.storekit_product_id] = name

    # Look up tier for this product
    new_tier_name = PRODUCT_TO_TIER.get(body.product_id)
    if not new_tier_name:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown product ID: {body.product_id}",
        )

    new_tier = tier_config.tiers[new_tier_name]
    old_tier_name = user.tier

    # Detect free trial: introductory offer with price 0
    is_trial = (
        body.offer_type == "introductory"
        and (body.offer_price is None or body.offer_price == 0)
    )

    now = datetime.now(timezone.utc)

    if is_trial:
        # Trial: use trial_cost_limit_usd, 7-day period
        trial_limit = new_tier.trial_cost_limit_usd or new_tier.monthly_cost_limit_usd
        resets_at = (now + timedelta(days=7)).isoformat()
        trial_end = resets_at

        await db.execute(
            """UPDATE users SET
                tier = ?,
                monthly_cost_limit_usd = ?,
                monthly_used_usd = 0,
                overage_balance_usd = 0,
                allocation_resets_at = ?,
                updated_at = ?,
                simulated_tier = NULL,
                simulated_exhausted = 0,
                is_trial = 1,
                trial_start = ?,
                trial_end = ?
               WHERE id = ?""",
            (
                new_tier_name,
                trial_limit,
                resets_at,
                now.isoformat(),
                now.isoformat(),
                trial_end,
                user.id,
            ),
        )
        await db.commit()

        return {
            "status": "ok",
            "old_tier": old_tier_name,
            "new_tier": new_tier_name,
            "is_trial": True,
            "trial_end": trial_end,
            "monthly_limit_usd": trial_limit,
            "allocation_resets_at": resets_at,
        }

    # Paid subscription (or trial-to-paid conversion)
    resets_at = (now + timedelta(days=30)).isoformat()

    await db.execute(
        """UPDATE users SET
            tier = ?,
            monthly_cost_limit_usd = ?,
            monthly_used_usd = 0,
            overage_balance_usd = 0,
            allocation_resets_at = ?,
            updated_at = ?,
            simulated_tier = NULL,
            simulated_exhausted = 0,
            is_trial = 0,
            trial_start = NULL,
            trial_end = NULL
           WHERE id = ?""",
        (
            new_tier_name,
            new_tier.monthly_cost_limit_usd,
            resets_at,
            now.isoformat(),
            user.id,
        ),
    )
    await db.commit()

    return {
        "status": "ok",
        "old_tier": old_tier_name,
        "new_tier": new_tier_name,
        "is_trial": False,
        "monthly_limit_usd": new_tier.monthly_cost_limit_usd,
        "allocation_resets_at": resets_at,
    }


class SyncSubscriptionRequest(BaseModel):
    """Sent by iOS app on launch to reconcile subscription state."""
    active_product_id: str | None = None  # Current entitlement, or null if none
    is_trial: bool = False


@router.post("/sync-subscription")
async def sync_subscription(
    body: SyncSubscriptionRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Reconcile the user's tier with their current StoreKit entitlement.

    Called by the iOS app on every launch. Handles:
    - Subscription cancelled: active_product_id is null → downgrade to free
    - Subscription active: active_product_id set → verify tier matches
    - Trial state: is_trial flag from StoreKit
    """
    tier_config = request.app.state.tier_config
    now = datetime.now(timezone.utc)

    if body.active_product_id is None:
        # No active subscription — downgrade to free if not already
        if user.tier == "free":
            return {"status": "ok", "action": "none", "tier": "free"}

        free_tier = tier_config.tiers.get("free")
        free_limit = free_tier.monthly_cost_limit_usd if free_tier else 0.05

        await db.execute(
            """UPDATE users SET
                tier = 'free',
                monthly_cost_limit_usd = ?,
                monthly_used_usd = ?,
                overage_balance_usd = 0,
                is_trial = 0,
                trial_start = NULL,
                trial_end = NULL,
                updated_at = ?
               WHERE id = ?""",
            (free_limit, free_limit, now.isoformat(), user.id),
        )
        await db.commit()

        return {
            "status": "ok",
            "action": "downgraded",
            "old_tier": user.tier,
            "new_tier": "free",
        }

    # Active subscription — build product-to-tier map
    global PRODUCT_TO_TIER
    if not PRODUCT_TO_TIER:
        for name, tier in tier_config.tiers.items():
            if tier.storekit_product_id:
                PRODUCT_TO_TIER[tier.storekit_product_id] = name

    expected_tier = PRODUCT_TO_TIER.get(body.active_product_id)
    if not expected_tier:
        return {"status": "ok", "action": "none", "tier": user.tier,
                "warning": f"Unknown product: {body.active_product_id}"}

    # Check if tier needs updating
    if user.tier == expected_tier and user.is_trial == body.is_trial:
        return {"status": "ok", "action": "none", "tier": user.tier}

    # Tier mismatch — update (e.g., trial ended and converted to paid)
    new_tier = tier_config.tiers[expected_tier]
    if body.is_trial and new_tier.trial_cost_limit_usd is not None:
        limit = new_tier.trial_cost_limit_usd
    else:
        limit = new_tier.monthly_cost_limit_usd

    # Trial-to-paid conversion: reset allocation for the first paid month.
    # The user was on a reduced trial allocation — now they're paying,
    # so they get a fresh full-month allocation.
    trial_converted = user.is_trial and not body.is_trial

    if trial_converted:
        resets_at = (now + timedelta(days=30)).isoformat()
        await db.execute(
            """UPDATE users SET
                tier = ?,
                monthly_cost_limit_usd = ?,
                monthly_used_usd = 0,
                overage_balance_usd = 0,
                allocation_resets_at = ?,
                is_trial = 0,
                trial_start = NULL,
                trial_end = NULL,
                updated_at = ?
               WHERE id = ?""",
            (expected_tier, limit, resets_at, now.isoformat(), user.id),
        )
    else:
        await db.execute(
            """UPDATE users SET
                tier = ?,
                monthly_cost_limit_usd = ?,
                is_trial = ?,
                updated_at = ?
               WHERE id = ?""",
            (expected_tier, limit, 1 if body.is_trial else 0, now.isoformat(), user.id),
        )

    await db.commit()

    result = {
        "status": "ok",
        "action": "updated",
        "old_tier": user.tier,
        "new_tier": expected_tier,
        "is_trial": body.is_trial,
    }
    if trial_converted:
        result["trial_converted"] = True
        result["monthly_limit_usd"] = limit
        result["allocation_resets_at"] = resets_at
    return result


@router.get("/usage/me")
async def usage_me(
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get the authenticated user's current allocation, overage balance, and usage."""
    tier_config = request.app.state.tier_config
    effective_tier_name = user.effective_tier
    tier = tier_config.tiers.get(effective_tier_name)

    # Use trial limit during active trial
    if tier and user.is_trial and tier.trial_cost_limit_usd is not None:
        monthly_limit = tier.trial_cost_limit_usd
    else:
        monthly_limit = tier.monthly_cost_limit_usd if tier else -1

    # When simulating exhausted, override allocation values
    is_simulated = user.simulated_tier is not None
    sim_exhausted = user.simulated_exhausted

    if sim_exhausted:
        monthly_used = monthly_limit
    else:
        # Read allocation state
        cursor = await db.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?",
            (user.id,),
        )
        row = await cursor.fetchone()
        monthly_used = float(row["monthly_used_usd"] or 0)

    # Read resets_at regardless (always from real data)
    cursor = await db.execute(
        "SELECT allocation_resets_at FROM users WHERE id = ?",
        (user.id,),
    )
    row = await cursor.fetchone()
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
    result = {
        "tier": effective_tier_name,
        "tier_display_name": tier.display_name if tier else effective_tier_name,
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
            "balance_usd": 0,
            "balance_hours": 0,
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
        "features": tier.features if tier else {},
    }

    if is_simulated:
        result["simulation"] = {
            "active": True,
            "simulated_tier": user.simulated_tier,
            "real_tier": user.tier,
            "exhausted": sim_exhausted,
        }

    # Trial state
    if user.is_trial and user.trial_end:
        result["is_trial"] = True
        result["trial_end"] = user.trial_end

    return result


@router.get("/tiers")
async def list_tiers(request: Request):
    """Return the full tier catalog for display in the iOS subscription UI.

    Public endpoint — no auth required. Returns descriptions, feature states,
    feature bullets, and constraint details for each tier. The iOS app
    uses this to render server-driven subscription screens instead of
    relying on hardcoded StoreKit descriptions.
    """
    tier_config = request.app.state.tier_config
    feature_config = request.app.state.feature_config

    # Build feature metadata (display names, descriptions, CTAs)
    feature_metadata = {}
    for fname, fdef in feature_config.features.items():
        feature_metadata[fname] = {
            "display_name": fdef.display_name,
            "description": fdef.description,
            "teaser_description": fdef.teaser_description,
            "upgrade_cta": fdef.upgrade_cta,
            "category": fdef.category,
        }

    tiers_result = {}
    for name, tier in tier_config.tiers.items():
        if name == "admin":
            continue  # Don't expose admin tier to clients
        tiers_result[name] = {
            "display_name": tier.display_name,
            "description": tier.description,
            "hours_per_month": tier.hours_per_month,
            "summary_mode": tier.summary_mode,
            "summary_interval_minutes": tier.summary_interval_minutes,
            "max_images_per_request": tier.max_images_per_request,
            "features": tier.features,
            "feature_bullets": tier.feature_bullets,
            "storekit_product_id": tier.storekit_product_id,
        }
    return {
        "tiers": tiers_result,
        "feature_definitions": feature_metadata,
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy an LLM request through GhostPour with auth, tier, and rate enforcement."""
    tier_config = request.app.state.tier_config
    provider_router = request.app.state.provider_router
    rate_limiter = request.app.state.rate_limiter
    usage_tracker = request.app.state.usage_tracker
    pricing = request.app.state.pricing

    # 1. Look up tier (respects simulation override)
    effective_tier_name = user.effective_tier
    tier = tier_config.tiers.get(effective_tier_name)
    if not tier:
        raise HTTPException(
            status_code=500,
            detail={"code": "invalid_request", "message": f"Unknown tier: {effective_tier_name}"},
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

    # 5.5. Context Quilt — generic feature gating
    #
    # Feature states from tiers.yml:
    #   enabled  → recall + inject into prompt + capture on response
    #   teaser   → recall only (return metadata for upgrade nudge, don't inject)
    #   disabled → skip entirely
    #
    # Client can send skip_teasers: ["context_quilt"] to opt out of teaser
    # checks after it has already shown the nudge this session.

    cq_state = tier.feature_state("context_quilt")
    cq_result = {"context": "", "matched_entities": [], "patch_count": 0}
    cq_gated = False  # True when teaser ran but results not injected

    skip_teasers = set(body.skip_teasers or [])

    if cq_state == "enabled" and body.context_quilt:
        # Full CQ: recall + inject
        cq_metadata = {}
        if body.project:
            cq_metadata["project"] = body.project
        cq_result = await cq.recall(
            user_id=user.id,
            text=body.user_content,
            metadata=cq_metadata or None,
        )
        if cq_result.get("context"):
            cq_context = cq_result["context"]
            if "{{context_quilt}}" in body.system_prompt:
                body = body.model_copy(update={
                    "system_prompt": body.system_prompt.replace("{{context_quilt}}", cq_context)
                })
            else:
                body = body.model_copy(update={
                    "system_prompt": f"[CONTEXT FROM PREVIOUS MEETINGS]\n{cq_context}\n\n{body.system_prompt}"
                })

    elif cq_state == "teaser" and "context_quilt" not in skip_teasers:
        # Teaser: recall for metadata only, don't inject into prompt
        cq_metadata = {}
        if body.project:
            cq_metadata["project"] = body.project
        cq_result = await cq.recall(
            user_id=user.id,
            text=body.user_content,
            metadata=cq_metadata or None,
        )
        if cq_result.get("matched_entities"):
            cq_gated = True

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
    await usage_tracker.record_cost(db, user.id, request_cost, tier, user=user)

    # 9. Log usage
    await usage_tracker.log_usage(db, user.id, body, response, elapsed_ms)

    # 9.5. Context Quilt capture (async, non-blocking) — only for enabled, not teaser
    if cq_state == "enabled" and body.context_quilt:
        asyncio.create_task(cq.capture(
            user_id=user.id,
            interaction_type=body.call_type or "query",
            content=body.user_content,
            response=response.text,
            meeting_id=body.meeting_id,
            project=body.project,
            call_type=body.call_type,
            prompt_mode=body.prompt_mode,
            display_name=user.display_name,
            email=user.email,
        ))

    # 10. Build response with allocation headers
    response_data = response.model_dump()
    json_response = JSONResponse(content=response_data)

    # Use trial limit for allocation headers during active trial
    effective_limit = tier.monthly_cost_limit_usd
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        effective_limit = tier.trial_cost_limit_usd

    if effective_limit != -1:
        new_monthly_used = monthly_used + request_cost
        percent = min(100, new_monthly_used / effective_limit * 100)
        json_response.headers["X-Allocation-Percent"] = f"{percent:.1f}"
        if percent >= 80:
            json_response.headers["X-Allocation-Warning"] = "true"
        json_response.headers["X-Monthly-Used"] = f"{new_monthly_used:.4f}"
        json_response.headers["X-Monthly-Limit"] = f"{effective_limit:.2f}"

    # Feature response headers — generic pattern for any gated feature
    matched = cq_result.get("matched_entities", [])
    if cq_state == "enabled" and body.context_quilt:
        # Full CQ: report what was used
        json_response.headers["X-CQ-Matched"] = str(len(matched))
        if matched:
            json_response.headers["X-CQ-Entities"] = ",".join(matched[:10])
    elif cq_gated:
        # Teaser: report what was found but not used
        json_response.headers["X-CQ-Matched"] = str(len(matched))
        json_response.headers["X-CQ-Gated"] = "true"
        if matched:
            json_response.headers["X-CQ-Entities"] = ",".join(matched[:10])

    return json_response


# MARK: - End-of-Meeting Transcript Capture


class TranscriptCaptureRequest(BaseModel):
    transcript: str
    meeting_id: str | None = None
    project: str | None = None


@router.post("/v1/capture-transcript")
async def capture_transcript(
    body: TranscriptCaptureRequest,
    user: UserRecord = Depends(get_current_user),
):
    """
    End-of-meeting transcript capture for Context Quilt.

    ShoulderSurf calls this at session end to send the full raw transcript.
    CQ extracts traits, preferences, and durable facts from the raw dialogue
    that would otherwise be lost in per-query summarization.
    """
    asyncio.create_task(cq.capture(
        user_id=user.id,
        interaction_type="meeting_transcript",
        content=body.transcript,
        meeting_id=body.meeting_id,
        project=body.project,
        display_name=user.display_name,
        email=user.email,
    ))
    return {"status": "queued"}
