import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Hard wall-clock ceiling for /v1/chat SSE streams. After this, we cancel
# the upstream task, emit a stream_timeout SSE error event, log a row in
# usage_log with status="timeout", and close the connection. Without this
# cap, a slow-trickle upstream could keep the connection open indefinitely
# (httpx's per-operation read timeout doesn't bound total wall-clock).
_CHAT_STREAM_WALL_CLOCK_SECONDS = 90

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest, ChatResponse
from app.models.user import UserRecord

router = APIRouter()


_PROJECT_CHAT_FALLBACK_TEASER = (
    "Project Chat is a Plus feature. "
    "Upgrade to ask AI questions across all your meetings in this project."
)


def _project_chat_teaser_response(request: Request) -> JSONResponse:
    """Build a canned upsell response when Project Chat is in teaser state.

    No LLM call, no allocation charge. Localized teaser_response is read
    from feature_definitions.project_chat in the active locale's tiers
    config; falls back to features.yml; finally to a hardcoded English
    string. Response shape mirrors a normal chat response so iOS renders
    the text as a regular AI bubble.
    """
    from app.routers.config import _parse_accept_language

    locale = _parse_accept_language(request.headers.get("Accept-Language"))
    configs = request.app.state.remote_configs
    localized_name = f"tiers.{locale}" if locale else None

    teaser_text: str | None = None
    for slug in (localized_name, "tiers"):
        if slug and slug in configs:
            pc = configs[slug].get("feature_definitions", {}).get("project_chat", {})
            if pc.get("teaser_response"):
                teaser_text = pc["teaser_response"]
                break

    if not teaser_text:
        feature_config = request.app.state.feature_config
        pc = feature_config.features.get("project_chat")
        if pc and pc.teaser_response:
            teaser_text = pc.teaser_response

    if not teaser_text:
        teaser_text = _PROJECT_CHAT_FALLBACK_TEASER

    headers = {"X-Locale-Resolved": locale or "en"} if locale else {}
    return JSONResponse(
        content={
            "text": teaser_text,
            "input_tokens": 0,
            "output_tokens": 0,
            "model": "ghostpour-canned",
            "provider": "ghostpour",
            # Sentinel — distinct from "standard" / "advanced" so iOS can
            # render server-generated upsell bubbles differently from real
            # AI responses. May later be split from "standard" at the badge
            # level if free-tier UX diverges further.
            "ai_tier": "free",
            "usage": {},
            "cost": {"total_cost": 0.0, "input_cost": 0.0, "output_cost": 0.0},
        },
        headers=headers,
    )


def _enforce_meeting_context_gate(
    remote_configs: dict, prompt_mode: str | None, meeting_id: str | None
) -> None:
    """Server-side enforcement of the protected-prompts context gate.

    When a `protected-prompts*` config has `requireMeetingContext: true`
    AND the requested prompt_mode is listed with `requiresContext: true`,
    require a non-empty meeting_id. Otherwise, raise 403.

    Iterates over all `protected-prompts*` configs (locale variants) so the
    gate works regardless of which locale the client's prompt name belongs
    to. The kill switch is per-config — flipping en/es/ja independently
    lets us roll out the gate per-locale if needed.
    """
    if not prompt_mode:
        return  # no prompt mode → nothing to gate
    if meeting_id:
        return  # context present → allowed regardless of gate state

    for slug, cfg in remote_configs.items():
        if "protected-prompts" not in slug:
            continue
        if not cfg.get("requireMeetingContext"):
            continue
        for mode in cfg.get("defaultPromptModes", []):
            if mode.get("name") == prompt_mode and mode.get("requiresContext"):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "context_required",
                        "message": (
                            f"Prompt mode '{prompt_mode}' requires meeting "
                            "context. Send a meeting_id with this request."
                        ),
                    },
                )


def _resolve_model_routing(
    request: Request, body: ChatRequest, tier, tier_name: str
) -> str | None:
    """Resolve which model to use for an 'auto' request.

    Checks the model-routing config (editable via admin dashboard):
      apps.<app_id>.call_types.<call_type>.models.<tier_name> → model

    Falls back to the tier's default_model if no routing match is found.
    """
    configs = request.app.state.remote_configs
    routing = configs.get("model-routing", {}).get("apps", {})

    if routing:
        app_id = getattr(request.state, "app_id", "unknown")
        call_type = body.get_meta("call_type")

        app_config = routing.get(app_id, {})
        if app_config and call_type:
            call_config = app_config.get("call_types", {}).get(call_type, {})
            if call_config:
                models = call_config.get("models", {})
                model = models.get(tier_name) or models.get("default")
                if model:
                    return model

    # Fall back to tier's default model
    return tier.default_model


# MARK: - StoreKit Receipt Verification


class VerifyReceiptRequest(BaseModel):
    product_id: str              # e.g., "com.example.myapp.sub.ultra.monthly"
    transaction_id: str          # StoreKit 2 original transaction ID
    signed_transaction: str | None = None  # JWS for future server-side verification
    offer_type: str | None = None  # "introductory" for free trial, None for paid
    offer_price: float | None = None  # 0.00 for free trial
    is_trial: bool | None = None  # Explicit trial flag from client (preferred over inference)


# Map StoreKit product IDs to tier names
PRODUCT_TO_TIER: dict[str, str] = {}  # Populated from tier config at startup


async def _placeholder_report_count(db: aiosqlite.Connection, user_id: str) -> int:
    """Count canned (budget-blocked) meeting reports for this user.
    Surfaced on /v1/verify-receipt so iOS can prompt regen for the most
    recent placeholder right after upgrade without scanning the meeting
    list. Cheap query (single integer)."""
    cursor = await db.execute(
        """SELECT COUNT(*) AS n FROM meeting_reports
           WHERE user_id = ? AND report_status = 'placeholder_budget_blocked'""",
        (user_id,),
    )
    row = await cursor.fetchone()
    return int(row["n"] or 0) if row else 0


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
            for product_id in tier.all_product_ids.values():
                if product_id:
                    PRODUCT_TO_TIER[product_id] = name

    # Look up tier for this product
    new_tier_name = PRODUCT_TO_TIER.get(body.product_id)
    if not new_tier_name:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown product ID: {body.product_id}",
        )

    new_tier = tier_config.tiers[new_tier_name]
    old_tier_name = user.tier

    # Detect free trial: prefer explicit flag from client, fall back to inference
    if body.is_trial is not None:
        is_trial = body.is_trial
    else:
        is_trial = (
            body.offer_type == "introductory"
            and (body.offer_price is None or body.offer_price == 0)
        )

    now = datetime.now(timezone.utc)

    # Determine if this is an idempotent re-verification or a real state change.
    # Reset allocation ONLY on:
    #   - New subscription (tier changed)
    #   - Trial → paid conversion
    # Do NOT reset on:
    #   - Re-verification of existing tier (SS calls this on every app launch)
    #   - Trial → trial (same state, just a periodic re-check)
    tier_changed = old_tier_name != new_tier_name
    trial_to_paid = user.is_trial and not is_trial
    is_state_change = tier_changed or trial_to_paid

    # Cross-account dedup: clear original_transaction_id from any OTHER user
    # row currently holding this transaction_id before binding it here.
    # Reachable via SS receipt-replay when a queued receipt lands under a
    # different signed-in JWT than the one it was originally verified under
    # (account switch on same device, or anon-purchase → later sign-in to a
    # different account). Without this, two rows hold the same id and the
    # apple-notifications webhook lookup only updates one of them.
    await db.execute(
        "UPDATE users SET original_transaction_id = NULL "
        "WHERE original_transaction_id = ? AND id != ?",
        (body.transaction_id, user.id),
    )

    if is_trial:
        # Trial: use trial_cost_limit_usd, 7-day period
        trial_limit = new_tier.trial_cost_limit_usd or new_tier.monthly_cost_limit_usd

        if is_state_change:
            # New trial — reset allocation and set trial window
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
                    trial_end = ?,
                    original_transaction_id = ?
                   WHERE id = ?""",
                (
                    new_tier_name, trial_limit, resets_at, now.isoformat(),
                    now.isoformat(), trial_end, body.transaction_id, user.id,
                ),
            )
            # Zero Project Chat quota counter on Free → trial upgrade so
            # the new subscriber doesn't start with stale counts that
            # would surface in feature_state on the first send.
            if old_tier_name == "free":
                from app.services.memory_capture_quota import zero_memory_quota_on_tier_change
                from app.services.project_chat_quota import zero_quota_on_tier_change
                await zero_quota_on_tier_change(db, user.id)
                await zero_memory_quota_on_tier_change(db, user.id)
        else:
            # Idempotent re-verification — only update limit, txn_id, and timestamp.
            # Preserve monthly_used_usd, allocation_resets_at, trial_start, trial_end.
            await db.execute(
                """UPDATE users SET
                    monthly_cost_limit_usd = ?,
                    updated_at = ?,
                    is_trial = 1,
                    original_transaction_id = ?
                   WHERE id = ?""",
                (trial_limit, now.isoformat(), body.transaction_id, user.id),
            )
        await db.commit()

        # Read back the preserved allocation_resets_at for the response
        cursor = await db.execute(
            "SELECT allocation_resets_at, trial_end FROM users WHERE id = ?",
            (user.id,),
        )
        row = await cursor.fetchone()
        return {
            "status": "ok",
            "old_tier": old_tier_name,
            "new_tier": new_tier_name,
            "is_trial": True,
            "trial_end": row["trial_end"] if row else None,
            "monthly_limit_usd": trial_limit,
            "allocation_resets_at": row["allocation_resets_at"] if row else None,
            "placeholder_report_count": await _placeholder_report_count(db, user.id),
        }

    # Paid subscription (or trial-to-paid conversion)
    if is_state_change:
        # Real upgrade/downgrade/conversion — reset allocation
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
                trial_end = NULL,
                original_transaction_id = ?
               WHERE id = ?""",
            (
                new_tier_name, new_tier.monthly_cost_limit_usd, resets_at,
                now.isoformat(), body.transaction_id, user.id,
            ),
        )
        # Zero Project Chat quota counter on Free → paid upgrade.
        if old_tier_name == "free":
            from app.services.project_chat_quota import zero_quota_on_tier_change
            await zero_quota_on_tier_change(db, user.id)
    else:
        # Idempotent re-verification — preserve allocation state
        await db.execute(
            """UPDATE users SET
                monthly_cost_limit_usd = ?,
                updated_at = ?,
                is_trial = 0,
                original_transaction_id = ?
               WHERE id = ?""",
            (
                new_tier.monthly_cost_limit_usd, now.isoformat(),
                body.transaction_id, user.id,
            ),
        )
    await db.commit()

    # Read back preserved allocation_resets_at
    cursor = await db.execute(
        "SELECT allocation_resets_at FROM users WHERE id = ?",
        (user.id,),
    )
    row = await cursor.fetchone()
    resets_at = row["allocation_resets_at"] if row else None

    return {
        "status": "ok",
        "old_tier": old_tier_name,
        "new_tier": new_tier_name,
        "is_trial": False,
        "monthly_limit_usd": new_tier.monthly_cost_limit_usd,
        "allocation_resets_at": resets_at,
        "placeholder_report_count": await _placeholder_report_count(db, user.id),
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
            for product_id in tier.all_product_ids.values():
                if product_id:
                    PRODUCT_TO_TIER[product_id] = name

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
    # Use hours_per_month from tier config (display value) rather than deriving from cost
    hours_limit = tier.hours_per_month if tier else -1

    # Credit-denominated allocation. iOS-facing UI should bind to these
    # rather than `hours.*` (which has marketing/cost drift) or
    # `monthly_*_usd` (which exposes vendor pricing). Conversion lives
    # server-side so we can shift the ratio without an iOS update.
    from app.services.budget_gate import dollars_to_credits
    if monthly_limit == -1:
        credits_total = -1
        credits_used = dollars_to_credits(monthly_used)
        credits_remaining = -1
    else:
        credits_total = dollars_to_credits(monthly_limit)
        credits_used = dollars_to_credits(monthly_used)
        credits_remaining = max(0, credits_total - credits_used)

    result = {
        "user_id": user.id,
        "tier": effective_tier_name,
        "tier_display_name": tier.display_name if tier else effective_tier_name,
        "allocation": {
            "monthly_limit_usd": monthly_limit,
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_remaining_usd": round(max(0, monthly_limit - monthly_used), 4) if monthly_limit != -1 else -1,
            "percent_used": round(monthly_used / monthly_limit * 100, 1) if monthly_limit > 0 else 0,
            "resets_at": resets_at,
        },
        "credits": {
            "used": credits_used,
            "total": credits_total,
            "remaining": credits_remaining,
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
        # App-specific tier constraints (nested for new clients, top-level for backwards compat)
        "app_config": {
            "summary_mode": tier.summary_mode if tier else "delta",
            "summary_interval_minutes": tier.summary_interval_minutes if tier else 10,
            "max_images_per_request": tier.max_images_per_request if tier else 0,
        },
        # Backwards compat: keep top-level fields for existing clients
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

    Public endpoint — no auth required. Supports localization via
    Accept-Language header — looks for a tiers.{lang} remote config,
    falls back to tiers config, then to tiers.yml.

    Display strings (display_name, description, feature_bullets) come from
    the remote config so they can be edited from the dashboard and translated.
    Structural data (hours, features, product IDs) comes from tiers.yml.
    """
    from app.routers.config import _parse_accept_language

    tier_config = request.app.state.tier_config
    feature_config = request.app.state.feature_config
    configs = request.app.state.remote_configs

    # Resolve localized tier display config
    locale = _parse_accept_language(request.headers.get("Accept-Language"))
    localized_name = f"tiers.{locale}" if locale else None
    display_config = None
    if localized_name and localized_name in configs:
        display_config = configs[localized_name]
    elif "tiers" in configs:
        display_config = configs["tiers"]

    display_tiers = display_config.get("tiers", {}) if display_config else {}
    display_features = display_config.get("feature_definitions", {}) if display_config else {}

    # Build feature metadata — prefer remote config, fall back to features.yml
    feature_metadata = {}
    for fname, fdef in feature_config.features.items():
        if fname in display_features:
            feature_metadata[fname] = display_features[fname]
            feature_metadata[fname]["category"] = fdef.category
        else:
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
            continue
        # Merge: display strings from remote config, structural from YAML
        dt = display_tiers.get(name, {})

        # Cost per hour for this tier's default model. Used by clients for
        # pre-flight allocation checks (estimated_minutes × cost_per_hour / 60).
        # Hardcoded by model family today; will become per-model when tiers
        # support mixed-model allocation.
        cost_per_hour_usd = 0.19 if "sonnet" in (tier.default_model or "").lower() else 0.05

        tier_entry = {
            "display_name": dt.get("display_name", tier.display_name),
            "description": dt.get("description", tier.description),
            "hours_per_month": tier.hours_per_month,
            "cost_per_hour_usd": cost_per_hour_usd,
            "monthly_cost_limit_usd": tier.monthly_cost_limit_usd,
            "summary_mode": tier.summary_mode,
            "summary_interval_minutes": tier.summary_interval_minutes,
            "max_images_per_request": tier.max_images_per_request,
            "features": tier.features,
            "feature_bullets": dt.get("feature_bullets", tier.feature_bullets),
            "storekit_product_id": tier.storekit_product_id,
        }
        # Structured display data from remote config (icon hints, status section)
        if "feature_items" in dt:
            tier_entry["feature_items"] = dt["feature_items"]
        if "status_items" in dt:
            tier_entry["status_items"] = dt["status_items"]
        tiers_result[name] = tier_entry

    response = {"tiers": tiers_result, "feature_definitions": feature_metadata}
    if locale:
        return JSONResponse(content=response, headers={"X-Config-Locale": locale})
    return response


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

    # 1.5. Project Chat policy gate. Re-resolves the verdict server-side
    # so we can't be tricked by a client that skipped /v1/features/project-chat/check.
    # See app/services/project_chat_policy.py and
    # docs/wire-contracts/project-chat.md for the full state matrix.
    project_chat_cta_kind = None
    project_chat_quota = None
    project_chat_pc_config = None
    project_chat_should_meter = False  # True when this send consumes a Free quota slot
    if body.get_meta("prompt_mode") == "ProjectChat":
        from app.routers.config import _parse_accept_language
        from app.routers.features import _get_project_chat_config
        from app.services.project_chat_policy import resolve_project_chat_verdict
        from app.services.project_chat_quota import read_quota_state

        _pc_locale = _parse_accept_language(request.headers.get("Accept-Language"))
        project_chat_pc_config = _get_project_chat_config(request, _pc_locale)
        gp_chat_flag = project_chat_pc_config.get("gp_chat_flag", "plus")
        free_quota_per_month = project_chat_pc_config.get("free_quota_per_month", 1)
        selected_model = body.get_meta("selected_model") or "ssai"
        if user.effective_tier == "free":
            project_chat_quota = read_quota_state(user, free_quota_per_month)
            has_quota = project_chat_quota.has_quota
        else:
            has_quota = True

        verdict = resolve_project_chat_verdict(
            is_logged_in=True,  # /v1/chat already requires JWT
            tier=user.effective_tier,
            gp_chat_flag=gp_chat_flag,
            selected_model=selected_model,  # type: ignore[arg-type]
            has_quota=has_quota,
            free_quota_per_month=free_quota_per_month,
        )

        if verdict.verdict == "login_required":
            # Should be unreachable since /v1/chat requires JWT, but defense
            # in depth in case auth changes.
            raise HTTPException(
                status_code=401,
                detail={"code": "login_required"},
            )
        if verdict.verdict == "send_to_user_model":
            raise HTTPException(
                status_code=422,
                detail={"code": "use_user_model"},
            )
        # send_to_gp / send_to_gp_with_cta both proceed through the normal
        # LLM path. Track the CTA kind so we can populate feature_state on
        # the response.
        project_chat_cta_kind = verdict.cta_kind

        # Decide whether this send consumes a metered Free quota slot.
        # Only the freebie verdict (`send_to_gp`) consumes a slot — the
        # `send_to_gp_with_cta` path is already over quota and would
        # double-decrement (skewing analytics) if metered again. The pill
        # clamps `remaining` at 0, so cosmetics are unaffected, but the
        # underlying counter must stop at `total`.
        if user.effective_tier == "free" and verdict.verdict == "send_to_gp":
            if gp_chat_flag == "plus":
                # 'plus' mode meters every Free send regardless of model.
                project_chat_should_meter = True
            elif gp_chat_flag in ("ssai", "ssai_free_only") and selected_model == "external":
                # ssai-family modes meter only the GP-overrides-your-model path.
                project_chat_should_meter = True
            # 'all' / 'logged_in' modes don't meter Free.

    # 1.6. Protected-prompts context gate. iOS already enforces requiresContext
    # client-side; this closes the bypass loophole when a non-iOS or modified
    # client sends a context-required prompt without a meeting_id. Activated
    # by flipping `requireMeetingContext: true` in the protected-prompts
    # config — kill switch is per-config (en/ja/es each can flip independently).
    _enforce_meeting_context_gate(
        request.app.state.remote_configs,
        body.get_meta("prompt_mode"),
        body.get_meta("meeting_id"),
    )

    # 2. Resolve "auto" model — check model-routing config first, then tier default
    if body.model == "auto" or body.provider == "auto":
        resolved_model = _resolve_model_routing(request, body, tier, effective_tier_name)
        if not resolved_model:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_request",
                    "message": "No default model configured for this tier",
                },
            )
        parts = resolved_model.split("/", 1)
        if len(parts) == 2:
            body = body.model_copy(update={"provider": parts[0], "model": parts[1]})
        else:
            body = body.model_copy(update={"model": resolved_model})

    # 2.5. Server-side prompt assembly — if client sent no system_prompt but
    # has a call_type with a registered prompt config, assemble it server-side.
    if not body.system_prompt:
        from app.services.prompt_assembly import assemble_prompt
        call_type = body.get_meta("call_type")
        if call_type:
            assembled = assemble_prompt(
                call_type, body.user_content, request.app.state.remote_configs
            )
            if assembled:
                updates = {
                    "system_prompt": assembled["system_prompt"],
                    "user_content": assembled["user_content"],
                }
                if assembled.get("max_tokens"):
                    updates["max_tokens"] = assembled["max_tokens"]
                body = body.model_copy(update=updates)

        if not body.system_prompt:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_request",
                    "message": "system_prompt is required (or send a call_type with a registered server-side prompt config)",
                },
            )

    # 2.6. Sanitize "(you)" suffixes from system prompt and user content.
    # SS sends [Name (you)] in transcript context to help CQ extraction,
    # but it must not reach the LLM in chat/summary/report prompts.
    from app.services.features.context_quilt_hook import _sanitize_you_suffix
    sanitized_system = _sanitize_you_suffix(body.system_prompt)
    sanitized_content = _sanitize_you_suffix(body.user_content)
    if sanitized_system != body.system_prompt or sanitized_content != body.user_content:
        body = body.model_copy(update={
            "system_prompt": sanitized_system,
            "user_content": sanitized_content,
        })

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

    # 5.5. Feature hooks (before LLM)
    #
    # Each registered feature hook runs before the LLM call and may modify
    # the request body. The hook_results dict is passed to after_llm and
    # response_headers after the LLM responds.
    feature_hooks = request.app.state.feature_hooks
    skip_teasers = set(body.skip_teasers or [])
    hook_results: dict[str, dict] = {}

    for feature_name, hook in feature_hooks.items():
        state = tier.feature_state(feature_name)
        if state != "disabled":
            body, result = await hook.before_llm(user, body, tier, state, skip_teasers)
            hook_results[feature_name] = result

    # Effective allocation limit (trial or regular)
    effective_limit = tier.monthly_cost_limit_usd
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        effective_limit = tier.trial_cost_limit_usd

    # 5.6. Pre-call gates — block before any LLM tokens are spent.
    # These run after feature hooks so they see the assembled prompt
    # (hooks may inject CQ recall, project context, etc.). They run
    # before the stream branch so the JSON envelope works on every
    # endpoint — SS's parser is content-type driven.
    from app.services.ai_tier import tier_to_ai_tier as _tier_to_ai_tier_lazy
    from app.services.budget_gate import (
        dollars_to_credits,
        estimate_call_cost_usd,
        estimate_input_tokens,
        would_exceed_budget,
    )
    is_project_chat_pre = body.get_meta("prompt_mode") == "ProjectChat"
    assembled_prompt = (body.system_prompt or "") + (body.user_content or "")
    estimated_input_tokens = estimate_input_tokens(assembled_prompt)

    # Context cap (Project Chat only). iOS already enforces client-side
    # via the tier max_input_tokens fuel gauge; this is the
    # defense-in-depth path for races / hacked clients / stale tiers.
    if is_project_chat_pre and tier.max_input_tokens != -1 and estimated_input_tokens > tier.max_input_tokens:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "context_too_large",
                "message": (
                    f"Selected context is too large for your tier "
                    f"({estimated_input_tokens} tokens, max {tier.max_input_tokens}). "
                    f"Deselect meetings or drop transcript chips."
                ),
                "feature_state": {
                    "feature": "project_chat",
                    "cta": {
                        "kind": "context_too_large",
                        "text": (
                            f"Selected context is {estimated_input_tokens // 1000}K tokens, "
                            f"over your {tier.max_input_tokens // 1000}K-token limit. "
                            f"Deselect meetings or drop transcripts to fit."
                        ),
                        "action": "trim_context",
                    },
                    "details": {
                        "max_tokens": tier.max_input_tokens,
                        "actual_tokens": estimated_input_tokens,
                        "tokenizer": "chars_div_4",
                    },
                },
            },
        )

    # Budget gate — pre-call cost estimate vs effective_limit + overage.
    # Skips when limit is unlimited (Plus/Pro/Admin) or when pricing data
    # isn't loaded (fail open to avoid blanket-blocking on a transient
    # outage; the post-call check_quota backstop still catches runaway
    # spend retroactively).
    if effective_limit != -1 and pricing.is_loaded:
        estimated_cost = estimate_call_cost_usd(
            pricing,
            provider=body.provider,
            model=body.model,
            input_tokens=estimated_input_tokens,
            max_output_tokens=body.max_tokens,
        )
        if estimated_cost is not None and would_exceed_budget(
            monthly_used_usd=monthly_used,
            estimated_cost_usd=estimated_cost,
            effective_limit_usd=effective_limit,
        ):
            credits_total = dollars_to_credits(effective_limit)
            credits_used = dollars_to_credits(monthly_used)
            credits_remaining = max(0, credits_total - credits_used)
            block_payload = {
                "text": "",
                "model": body.model,
                "provider": body.provider,
                "ai_tier": _tier_to_ai_tier_lazy(user.effective_tier),
                "feature_state": {
                    "feature": "chat" if not is_project_chat_pre else "project_chat",
                    "credits_remaining": credits_remaining,
                    "credits_total": credits_total,
                    "credits_resets_at": user.allocation_resets_at,
                    "cta": {
                        "kind": "budget_exhausted",
                        "text": "You've used your free AI for this month. Upgrade to Plus to keep going.",
                        "action": "open_paywall",
                    },
                },
            }
            return JSONResponse(status_code=200, content=block_payload)

    # 6. Stream or non-stream based on request + call_type
    # Only stream interactive queries; background tasks (summary, analysis) get full JSON.
    # Project Chat is also forced non-streaming so feature_state can land
    # cleanly in the JSON body (SSE injection of structured trailer fields
    # would require a separate event type and client-side merge).
    call_type = body.get_meta("call_type")
    is_project_chat = body.get_meta("prompt_mode") == "ProjectChat"
    should_stream = (
        body.stream
        and call_type not in ("summary", "analysis")
        and not is_project_chat
    )

    if should_stream:
        return await _handle_stream(
            body, request, user, db, provider_router, usage_tracker,
            pricing, tier, feature_hooks, hook_results,
            monthly_used, overage_balance, effective_limit,
        )

    # --- Non-streaming path (original) ---

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

    # 9.5. Feature hooks (after LLM) — async, non-blocking
    for feature_name, hook in feature_hooks.items():
        state = tier.feature_state(feature_name)
        if feature_name in hook_results:
            await hook.after_llm(user, body, response, hook_results[feature_name], state)

    # Server-controlled tier label. Decoupled from `response.model` so we
    # can swap models per tier without breaking iOS attribution UI.
    from app.services.ai_tier import tier_to_ai_tier
    response.ai_tier = tier_to_ai_tier(effective_tier_name)

    # 10. Build response with allocation headers
    response_data = response.model_dump()

    # Project Chat: populate feature_state in the response, and decrement
    # the per-user counter for any Free send that consumes a metered slot.
    # The decrement happens after the LLM call so we don't burn quota on
    # upstream failures (which raise above before reaching this point).
    if body.get_meta("prompt_mode") == "ProjectChat" and project_chat_pc_config is not None:
        from app.services.project_chat_policy import render_cta_text
        from app.services.project_chat_quota import decrement_quota, read_quota_state

        feature_state = {
            "feature": "project_chat",
            "policy_mode": project_chat_pc_config.get("gp_chat_flag", "plus"),
        }

        # Decrement first if this send consumed a metered slot. Fires only
        # on the freebie verdict (`send_to_gp`) — `send_to_gp_with_cta` is
        # already over quota and must not double-decrement.
        if project_chat_should_meter:
            await decrement_quota(db, user.id)
            await db.commit()

        # Surface fresh quota numbers to iOS for any Free send (with or
        # without CTA) so the counter pill reflects the post-send state.
        if user.effective_tier == "free":
            free_quota_per_month = project_chat_pc_config.get("free_quota_per_month", 1)
            cursor = await db.execute(
                "SELECT project_chat_used_this_period, project_chat_period FROM users WHERE id = ?",
                (user.id,),
            )
            row = await cursor.fetchone()
            user_post = user.model_copy(update={
                "project_chat_used_this_period": int(row["project_chat_used_this_period"] or 0),
                "project_chat_period": row["project_chat_period"],
            })
            quota_post = read_quota_state(user_post, free_quota_per_month)
            feature_state["quota_remaining"] = (
                quota_post.remaining if quota_post.remaining is not None else None
            )
            feature_state["quota_total"] = quota_post.total
            feature_state["quota_resets_at"] = quota_post.resets_at

            if project_chat_cta_kind is not None:
                cta_strings = project_chat_pc_config.get("cta_strings", {})
                cta_text = render_cta_text(
                    project_chat_cta_kind,
                    cta_strings,
                    remaining=quota_post.remaining if quota_post.remaining is not None else 0,
                    total=quota_post.total,
                )
                feature_state["cta"] = {
                    "kind": project_chat_cta_kind,
                    "text": cta_text,
                }

        response_data["feature_state"] = feature_state

    json_response = JSONResponse(content=response_data)

    if effective_limit != -1:
        new_monthly_used = monthly_used + request_cost
        percent = min(100, new_monthly_used / effective_limit * 100)
        json_response.headers["X-Allocation-Percent"] = f"{percent:.1f}"
        if percent >= 80:
            json_response.headers["X-Allocation-Warning"] = "true"
        json_response.headers["X-Monthly-Used"] = f"{new_monthly_used:.4f}"
        json_response.headers["X-Monthly-Limit"] = f"{effective_limit:.2f}"

    # Feature response headers
    for feature_name, hook in feature_hooks.items():
        if feature_name in hook_results:
            state = tier.feature_state(feature_name)
            for k, v in hook.response_headers(hook_results[feature_name], state).items():
                json_response.headers[k] = v

    return json_response


async def _handle_stream(
    body, request, user, db, provider_router, usage_tracker,
    pricing, tier, feature_hooks, hook_results,
    monthly_used, overage_balance, effective_limit,
):
    """SSE streaming path for interactive chat queries.

    Streams text deltas as they arrive from the provider. Cost recording,
    usage logging, and after_llm hooks run after the stream completes.

    Note: The generator opens its own DB connection because FastAPI's
    request-scoped Depends(get_db) closes before the generator finishes.
    """
    from app.database import get_db as _get_db
    # Pre-compute allocation headers (sent before any body chunks)
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if effective_limit != -1:
        percent = min(100, monthly_used / effective_limit * 100)
        headers["X-Allocation-Percent"] = f"{percent:.1f}"
        if percent >= 80:
            headers["X-Allocation-Warning"] = "true"
        headers["X-Monthly-Used"] = f"{monthly_used:.4f}"
        headers["X-Monthly-Limit"] = f"{effective_limit:.2f}"

    # Feature hook headers
    for feature_name, hook in feature_hooks.items():
        if feature_name in hook_results:
            state = tier.feature_state(feature_name)
            for k, v in hook.response_headers(hook_results[feature_name], state).items():
                headers[k] = v

    start = time.monotonic()

    async def event_stream():
        final_response = None
        try:
            async with asyncio.timeout(_CHAT_STREAM_WALL_CLOCK_SECONDS):
                async for event in provider_router.route_stream(body):
                    if event.get("done"):
                        final_response = event.get("response")
                    else:
                        # Yield text delta as SSE
                        sse_data = json.dumps({"type": "text", "text": event["text"]})
                        yield f"data: {sse_data}\n\n"

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            async for err_db in _get_db():
                await usage_tracker.log_usage(
                    err_db, user.id, body, None, elapsed_ms, status="timeout"
                )
            timeout_data = json.dumps({
                "type": "error",
                "code": "stream_timeout",
                "message": f"Stream exceeded {_CHAT_STREAM_WALL_CLOCK_SECONDS}s cap.",
            })
            yield f"data: {timeout_data}\n\n"
            return

        except HTTPException:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            async for err_db in _get_db():
                await usage_tracker.log_usage(
                    err_db, user.id, body, None, elapsed_ms, status="error"
                )
            error_data = json.dumps({"type": "error", "text": "Provider error"})
            yield f"data: {error_data}\n\n"
            return

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Post-stream: cost calculation, recording, logging, hooks.
        # Use a fresh DB connection — the request-scoped one is closed by now.
        request_cost = 0.0
        if final_response and pricing.is_loaded:
            cost = pricing.calculate_cost(
                provider=body.provider,
                model=body.model,
                usage=final_response.usage,
                input_tokens=final_response.input_tokens,
                output_tokens=final_response.output_tokens,
            )
            final_response.cost = cost
            request_cost = cost.get("total_cost", 0.0)

        async for stream_db in _get_db():
            await usage_tracker.record_cost(stream_db, user.id, request_cost, tier, user=user)
            await usage_tracker.log_usage(stream_db, user.id, body, final_response, elapsed_ms)

            for feature_name, hook in feature_hooks.items():
                state = tier.feature_state(feature_name)
                if feature_name in hook_results:
                    await hook.after_llm(user, body, final_response, hook_results[feature_name], state)

        from app.services.ai_tier import tier_to_ai_tier

        # Final event with metadata (tokens, cost, allocation)
        done_data = {
            "type": "done",
            "input_tokens": final_response.input_tokens if final_response else None,
            "output_tokens": final_response.output_tokens if final_response else None,
            "cost": final_response.cost if final_response else None,
            "usage": final_response.usage if final_response else None,
            "ai_tier": tier_to_ai_tier(user.effective_tier),
        }
        if effective_limit != -1:
            new_used = monthly_used + request_cost
            done_data["allocation_percent"] = min(100, new_used / effective_limit * 100)
        yield f"data: {json.dumps(done_data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )
