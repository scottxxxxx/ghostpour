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
_CHAT_STREAM_WALL_CLOCK_SECONDS = 180

# How long the stream may stay silent before we emit a progress heartbeat.
# Lets a client keep an honest "still working" indicator alive through the
# pre-first-token gap (model thinking / queued / running web_search) without
# us fabricating a completion fraction we can't actually know.
_STREAM_HEARTBEAT_SECONDS = 10


def _strip_json_code_fence(text: str) -> str:
    """Unwrap a response that is wholly a ```code fence``` wrapping valid JSON.

    Several managed JSON call types tell the model "no code fences," but it
    often wraps the object anyway (```json … ```), forcing the client to
    strip it. We do it server-side instead. Content-driven and conservative:
    we only unwrap when the ENTIRE response is a single fenced block whose
    inner content parses as JSON, so prose/markdown answers (which may contain
    a legitimate code block, or be a non-JSON brief) are never altered.
    """
    if not text:
        return text
    s = text.strip()
    if not (s.startswith("```") and s.endswith("```")):
        return text
    inner = s[3:-3].strip()
    # The opening fence may carry a language tag on its own first line
    # (```json). Drop it only when it looks like a tag, not the start of JSON.
    if "\n" in inner:
        first, rest = inner.split("\n", 1)
        ft = first.strip()
        if ft and " " not in ft and "{" not in ft and "[" not in ft:
            inner = rest
    inner = inner.strip()
    try:
        json.loads(inner)
    except Exception:
        return text
    return inner


import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest, ChatResponse
from app.models.user import UserRecord
from app.services import context_quilt as cq
from app.services.allocation_reset import compute_next_reset, lazy_reset_if_due

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

    Looks up the model-routing config (editable via admin dashboard):
      apps.<app_id>.call_types.<call_type>.models.<tier_name> → model

    Two-stage lookup:

    1. **Surface-aware preference** — when `prompt_mode` identifies a
       chat surface (ProjectChat / PostMeetingChat), prefer the
       surface's dedicated dial. Within a surface, follow-ups use the
       `<surface>_follow_up` row when iOS sends the matching
       `call_type`; otherwise the first-send dial. This lets the
       dashboard dial each (surface × first/follow-up) cell
       independently without requiring iOS to send an exotic
       call_type for every send.

    2. **Direct call_type lookup** — for surfaces without a dedicated
       prompt_mode (Copilot / freeform / TR app), use the call_type
       row as-is. This is also the fallback when the surface dial
       above doesn't have an entry for the tier.

    Falls back to `tier.default_model` if neither lookup finds a row.

    See `docs/wire-contracts/model-routing-call-types.md` for the
    iOS-side spec.
    """
    configs = request.app.state.remote_configs
    routing = configs.get("model-routing", {}).get("apps", {})

    if not routing:
        return tier.default_model

    app_id = getattr(request.state, "app_id", "unknown")
    call_type = body.get_meta("call_type")
    prompt_mode = body.get_meta("prompt_mode")
    app_config = routing.get(app_id, {})
    if not app_config:
        return tier.default_model

    call_types_cfg = app_config.get("call_types", {})

    def _model_from_row(row_name: str) -> str | None:
        row = call_types_cfg.get(row_name)
        if not row:
            return None
        models = row.get("models", {})
        return models.get(tier_name) or models.get("default")

    # 1. Surface-aware preference. Each surface has a (first, follow_up)
    #    pair of dials; iOS picks which by setting `call_type` to the
    #    follow-up name. If iOS hasn't migrated yet, the follow-up name
    #    won't match and we fall through to the first-send dial — which
    #    preserves PR #161 behavior for legacy clients sending
    #    call_type=query inside ProjectChat.
    surface_dials = {
        "ProjectChat": ("project_chat", "project_chat_follow_up"),
        "PostMeetingChat": ("meeting_chat", "meeting_chat_follow_up"),
    }
    surface = surface_dials.get(prompt_mode)
    if surface is not None:
        first_row, follow_up_row = surface
        # Documents upgrade the turn (decided 2026-07-10): a send carrying
        # the documents field resolves through the surface's FIRST-SEND dial
        # even when iOS marks it a follow-up. Reading a document is
        # first-class work — and the cheap follow-up lane's provider-side
        # PDF page ceiling is lower than the served passthrough caps assume,
        # so document turns must ride the full-model lane for the caps to
        # stay coherent.
        # Confirmed generation turns upgrade the turn too (same coherence
        # rule): the artifact's quality rides the first-send lane, not
        # whatever lane the follow-up happened to be on.
        if (call_type == follow_up_row and not body.documents
                and not body.get_meta("generation_confirmed")):
            model = _model_from_row(follow_up_row)
            if model:
                return model
            # Follow-up row missing this tier — defensive fallback to the
            # surface's first-send dial rather than silently routing to
            # the unrelated `query` row.
        model = _model_from_row(first_row)
        if model:
            return model
        # Surface row exists but has no entry for this tier; fall through
        # to direct call_type lookup so the request still resolves.

    # 2. Direct call_type lookup (Copilot / freeform / TR / fallback).
    if call_type:
        model = _model_from_row(call_type)
        if model:
            return model

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
            # Zero Memory capture quota counter on Free → trial upgrade so
            # the new subscriber doesn't start with stale counts that
            # would surface on the first capture.
            if old_tier_name == "free":
                from app.services.memory_capture_quota import zero_memory_quota_on_tier_change
                await zero_memory_quota_on_tier_change(db, user.id)
            asyncio.create_task(cq.notify_tier_change(
                user_id=user.id,
                old_tier=old_tier_name,
                new_tier=new_tier_name,
                event_type="trial_start",
            ))
            # History log: a trial start is a subscription in Apple's eyes (an
            # introductory offer), so it marks the user as no longer a "new
            # subscriber" for offer-code eligibility. Revenue for the trial
            # period is $0.
            try:
                from app.services import subscriptions as subs
                await subs.record_subscription_event(
                    db, user_id=user.id, event_type="subscribed", subtype="trial",
                    from_tier=old_tier_name, to_tier=new_tier_name,
                    product_id=body.product_id, transaction_id=body.transaction_id,
                    original_transaction_id=body.transaction_id,
                    source="verify_receipt", price_usd=0.0,
                )
            except Exception as e:
                logger.warning("subscription_event (trial) record failed: %s", e)
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

        # A verified receipt (even an idempotent re-verify) means this user has
        # a subscription, so mark the eligibility cache. Undated: don't overwrite
        # a known first_subscribed_at; reconcile fills the real date from Apple.
        try:
            from app.services import subscriptions as subs
            await subs.mark_ever_subscribed(db, user.id)
        except Exception as e:
            logger.warning("mark_ever_subscribed (trial) failed: %s", e)

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
        # Real upgrade/downgrade/conversion — reset allocation. The Apple
        # webhook will keep allocation_resets_at aligned with Apple's
        # billing on subsequent renewals; this initial value is the
        # 1-month fallback used until the first DID_RENEW lands.
        resets_at = compute_next_reset(now).isoformat()
        await db.execute(
            """UPDATE users SET
                tier = ?,
                monthly_cost_limit_usd = ?,
                monthly_used_usd = 0,
                overage_balance_usd = 0,
                searches_used = 0,
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
        if trial_to_paid:
            event_type = "trial_to_paid"
        else:
            tier_rank = {"free": 0, "plus": 1, "pro": 2, "admin": 3}
            event_type = (
                "upgrade"
                if tier_rank.get(new_tier_name, 0) >= tier_rank.get(old_tier_name, 0)
                else "downgrade"
            )
        asyncio.create_task(cq.notify_tier_change(
            user_id=user.id,
            old_tier=old_tier_name,
            new_tier=new_tier_name,
            event_type=event_type,
        ))
        # History log: record the paid state change (new sub, trial conversion,
        # upgrade, or downgrade). Re-verifications (no state change) are skipped
        # so the log isn't spammed on every app launch.
        try:
            from app.services import subscriptions as subs
            _rank = {"free": 0, "plus": 1, "pro": 2, "admin": 3}
            if trial_to_paid or old_tier_name in ("free", None):
                _sub_evt = "subscribed"
            elif _rank.get(new_tier_name, 0) >= _rank.get(old_tier_name, 0):
                _sub_evt = "upgraded"
            else:
                _sub_evt = "downgraded"
            await subs.record_subscription_event(
                db, user_id=user.id, event_type=_sub_evt,
                subtype="conversion" if trial_to_paid else None,
                from_tier=old_tier_name, to_tier=new_tier_name,
                product_id=body.product_id, transaction_id=body.transaction_id,
                original_transaction_id=body.transaction_id,
                source="verify_receipt",
            )
        except Exception as e:
            logger.warning("subscription_event (paid) record failed: %s", e)
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

    # A verified paid receipt (incl. idempotent re-verify) means this user is a
    # subscriber — mark the eligibility cache. Undated so it won't overwrite a
    # known first_subscribed_at; reconcile backfills the real date from Apple.
    try:
        from app.services import subscriptions as subs
        await subs.mark_ever_subscribed(db, user.id)
    except Exception as e:
        logger.warning("mark_ever_subscribed (paid) failed: %s", e)

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

        asyncio.create_task(cq.notify_tier_change(
            user_id=user.id,
            old_tier=user.tier,
            new_tier="free",
            event_type="cancellation",
        ))

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
        resets_at = compute_next_reset(now).isoformat()
        await db.execute(
            """UPDATE users SET
                tier = ?,
                monthly_cost_limit_usd = ?,
                monthly_used_usd = 0,
                overage_balance_usd = 0,
                searches_used = 0,
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

    if trial_converted:
        event_type = "trial_to_paid"
    else:
        tier_rank = {"free": 0, "plus": 1, "pro": 2, "admin": 3}
        event_type = (
            "upgrade"
            if tier_rank.get(expected_tier, 0) >= tier_rank.get(user.tier, 0)
            else "downgrade"
        )
    asyncio.create_task(cq.notify_tier_change(
        user_id=user.id,
        old_tier=user.tier,
        new_tier=expected_tier,
        event_type=event_type,
    ))

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

    # Read resets_at + searches_used in one shot — both used by the
    # search section of the response. Searches counter is needed so iOS
    # can render an "N of M used this month" pill without firing a
    # search-enabled request first.
    cursor = await db.execute(
        "SELECT allocation_resets_at, searches_used FROM users WHERE id = ?",
        (user.id,),
    )
    row = await cursor.fetchone()
    resets_at = row["allocation_resets_at"] if row else None
    searches_used_count = int(row["searches_used"] or 0) if row else 0

    # Resolve per-tier search caps for the search block of the response.
    # Locale-agnostic (admin dashboard reads en); the body strings live
    # in the CTA payload that's only rendered on the chat path, not here.
    from app.services.search_caps import get_search_caps as _gsc
    _search_caps = _gsc(
        request.app.state.remote_configs, effective_tier_name, locale=None,
    )

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

    # Per-app tier overrides (#249): replace specific tier values for the
    # calling app (e.g. TR caps max_images at 1). {} for SS / no header, so the
    # tier value is left untouched there.
    from app.routers.config import tier_overrides_for_app
    _app_overrides = tier_overrides_for_app(getattr(request.state, "app_id", None))
    _img_cap = _app_overrides.get(
        "max_images_per_request", tier.max_images_per_request if tier else 0
    )

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
            "max_images_per_request": _img_cap,
        },
        # Search caps + live counter. Lets iOS render an "X of Y used
        # this month" pill near the search toggle WITHOUT firing a
        # request first. The counter is the same field the chat-router
        # gate reads, so this and the search_state sidecar stay in sync.
        # `total: 0` means the tier has no search at all (Free).
        "search": {
            "used": searches_used_count,
            "total": _search_caps.searches_per_month,
            "soft_threshold": _search_caps.searches_soft_threshold,
            "resets_at": resets_at,
        },
        # Backwards compat: keep top-level fields for existing clients
        "summary_mode": tier.summary_mode if tier else "delta",
        "summary_interval_minutes": tier.summary_interval_minutes if tier else 10,
        "max_images_per_request": _img_cap,
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

    # Marketing email opt-in state — part of the SS startup query so the
    # iOS Settings toggle reflects the latest value (e.g., flips after
    # an email-side unsubscribe or a spam complaint).
    from app.services import marketing_opt_in as marketing
    moi = await marketing.get_marketing_opt_in(db, user.id)
    result["marketing_opt_in"] = {
        "enabled": moi["opt_in"],
        "updated_at": moi["updated_at"],
        "source": moi["source"],
    }

    # Budget-exhausted CTA payload — surfaces when the user is past their
    # monthly cap so iOS can render the upgrade prompt on pre-flight gates
    # (e.g., the meeting-start check) without firing a /v1/chat call first.
    # Same canonical CTA shape the /v1/chat block-response emits. Omitted
    # for unlimited tiers (monthly_limit == -1) since they never exhaust.
    if credits_total != -1 and credits_remaining <= 0:
        from app.routers.config import _parse_accept_language
        from app.services.budget_cta import get_budget_exhausted_cta
        locale = _parse_accept_language(request.headers.get("Accept-Language"))
        result["budget_exhausted_cta"] = get_budget_exhausted_cta(
            request.app.state.remote_configs,
            effective_tier_name,
            locale,
        )

    return result


# --- Timing hints ---------------------------------------------------------
# Per-call_type latency + output-size percentiles, computed from usage_log,
# so a client can drive an HONEST progress indicator: a curve shaped to what
# we've actually measured, never a countdown to a finish we can't predict.
# When the call streams, the same expected-output-token figure scales the
# real token-flow signal; when it doesn't, the curve is the fallback. The
# hints are aggregated per call_type and NEVER per model, so they can't leak
# which model we picked (the ghost-relay opacity rule).
_TIMING_HINTS_WINDOW_DAYS = 30
_TIMING_HINTS_MIN_SAMPLES = 5
_TIMING_HINTS_TTL_SEC = 600
# Cache keyed by app_id → (computed_at_monotonic, payload). Slow-changing
# aggregate, so a short TTL keeps the per-request DB scan off the hot path.
_timing_hints_cache: dict[str, tuple[float, dict]] = {}


def _percentile(sorted_vals: list[int], p: float) -> int:
    """Nearest-rank percentile of a pre-sorted list (matches dashboard math)."""
    if not sorted_vals:
        return 0
    idx = int(len(sorted_vals) * p)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


@router.get("/timing-hints")
async def timing_hints(
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Per-call_type expected-duration hints for honest progress UI.

    For each call_type the caller's app has run, returns the p50/p90 of
    response_time_ms and the p50 of output_tokens over the last N days of
    successful requests. A client uses these to render progress
    proportionate to what we expect — and, when streaming, to scale the
    live token-flow against an expected total — instead of a countdown to
    an unpredictable finish.

    Scoped to the caller's app_id when known, so each app sees its own
    call types. Aggregated across models on purpose: we never expose
    per-model timing, which would reveal which model we chose.
    """
    app_id = getattr(request.state, "app_id", "unknown")

    now = time.monotonic()
    cached = _timing_hints_cache.get(app_id)
    if cached and (now - cached[0]) < _TIMING_HINTS_TTL_SEC:
        return cached[1]

    # Pull raw per-call_type samples; percentiles are computed in Python
    # (the row volume is small and SQLite has no native percentile aggregate).
    sql = (
        "SELECT call_type, response_time_ms, output_tokens FROM usage_log "
        "WHERE status = 'success' AND response_time_ms IS NOT NULL "
        "AND call_type IS NOT NULL "
        "AND request_timestamp >= date('now', ?)"
    )
    params: list[object] = [f"-{_TIMING_HINTS_WINDOW_DAYS} days"]
    if app_id and app_id != "unknown":
        sql += " AND app_id = ?"
        params.append(app_id)
    cursor = await db.execute(sql, tuple(params))
    rows = await cursor.fetchall()

    buckets: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        b = buckets.setdefault(r["call_type"], {"ms": [], "out": []})
        b["ms"].append(int(r["response_time_ms"]))
        if r["output_tokens"] is not None:
            b["out"].append(int(r["output_tokens"]))

    hints = {}
    for ct, b in buckets.items():
        if len(b["ms"]) < _TIMING_HINTS_MIN_SAMPLES:
            continue  # too few to be honest about; client falls back to its default
        ms = sorted(b["ms"])
        out = sorted(b["out"])
        hints[ct] = {
            "p50_ms": _percentile(ms, 0.50),
            "p90_ms": _percentile(ms, 0.90),
            "p50_output_tokens": _percentile(out, 0.50) if out else None,
            "samples": len(ms),
        }

    payload = {"window_days": _TIMING_HINTS_WINDOW_DAYS, "hints": hints}
    _timing_hints_cache[app_id] = (now, payload)
    return payload


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

    # Per-app tier overrides (#249): TR caps max_images at 1 across the
    # catalog. {} for SS / no header → tier values served unchanged.
    from app.routers.config import tier_overrides_for_app
    app_overrides = tier_overrides_for_app(getattr(request.state, "app_id", None))

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
            "max_images_per_request": app_overrides.get(
                "max_images_per_request", tier.max_images_per_request
            ),
            "features": tier.features,
            "feature_bullets": dt.get("feature_bullets", tier.feature_bullets),
            "storekit_product_id": tier.storekit_product_id,
        }
        # Structured display data from remote config (icon hints, status section)
        if "feature_items" in dt:
            tier_entry["feature_items"] = dt["feature_items"]
        if "status_items" in dt:
            tier_entry["status_items"] = dt["status_items"]
        # Per-tier tunables passed through from tiers.json's feature_definitions
        # block. Legacy: feature_definitions.project_chat.max_input_tokens.
        # New iOS clients should prefer /v1/config/client-config (locale-aware
        # max_input_chars); this field is kept for back-compat with iOS builds
        # that haven't migrated. Pass-through, not a re-shape.
        if "feature_definitions" in dt:
            tier_entry["feature_definitions"] = dt["feature_definitions"]
        tiers_result[name] = tier_entry

    # Stamp the response with the version of the live tiers config so iOS can
    # tell at a glance which payload it's looking at — useful when an edit
    # is ambiguous between "not deployed yet" and "deployed but iOS cached."
    response: dict[str, object] = {
        "tiers": tiers_result,
        "feature_definitions": feature_metadata,
    }
    if display_config and "version" in display_config:
        response["version"] = display_config["version"]
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
    # App identity (X-App-ID, set by middleware) — threaded into every
    # usage_log write so analytics can be split per app. Captured by the
    # nested event_stream() closure too.
    app_id = getattr(request.state, "app_id", "unknown")

    # Stamp the middleware-minted X-Request-ID into the request meta bag so
    # log_usage lands it in usage_log metadata — partner harnesses quote this
    # response header verbatim when reporting runs, and until now it matched
    # nothing we store. Overwrites any client-sent value: the server-minted
    # id is the one on the wire.
    _rid = getattr(request.state, "request_id", None)
    if _rid:
        if body.metadata is None:
            body.metadata = {}
        body.metadata["request_id"] = _rid

    # 1. Look up tier (respects simulation override)
    effective_tier_name = user.effective_tier
    tier = tier_config.tiers.get(effective_tier_name)
    if not tier:
        raise HTTPException(
            status_code=500,
            detail={"code": "invalid_request", "message": f"Unknown tier: {effective_tier_name}"},
        )

    # 1.5. Project Chat policy gate. Re-resolves the routing verdict
    # server-side so we can't be tricked by a client that skipped
    # /v1/features/project-chat/check. The budget gate handles Free-tier
    # blocking — this gate is purely about routing.
    if body.get_meta("prompt_mode") == "ProjectChat":
        from app.routers.config import _parse_accept_language
        from app.routers.features import _get_project_chat_config
        from app.services.project_chat_policy import resolve_project_chat_verdict

        _pc_locale = _parse_accept_language(request.headers.get("Accept-Language"))
        _pc_config = _get_project_chat_config(request, _pc_locale)
        verdict = resolve_project_chat_verdict(
            is_logged_in=True,  # /v1/chat already requires JWT
            tier=user.effective_tier,
            gp_chat_flag=_pc_config.get("gp_chat_flag", "plus"),
            selected_model=body.get_meta("selected_model") or "ssai",
        )

        if verdict.verdict == "login_required":
            raise HTTPException(
                status_code=401,
                detail={"code": "login_required"},
            )
        if verdict.verdict == "send_to_user_model":
            raise HTTPException(
                status_code=422,
                detail={"code": "use_user_model"},
            )

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
    managed_routing = body.model == "auto" or body.provider == "auto"
    if managed_routing:
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
                call_type,
                body.user_content,
                request.app.state.remote_configs,
                prompt_mode=body.get_meta("prompt_mode"),
                scenario_kind=body.get_meta("scenario_kind"),
                scenario=body.get_meta("scenario"),
            )
            if assembled:
                updates = {
                    "system_prompt": assembled["system_prompt"],
                    "user_content": assembled["user_content"],
                }
                if assembled.get("max_tokens"):
                    updates["max_tokens"] = assembled["max_tokens"]
                if assembled.get("temperature") is not None:
                    updates["temperature"] = assembled["temperature"]
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

    # 3. Check provider + model access. The provider/model allowlists are a
    # BYOK guard on user-pinned targets; a managed (auto) call is routed by GP
    # to a tier-appropriate target via model-routing, so it must not be re-gated
    # here (e.g. tr_research_company -> openrouter/sonar, which no customer tier
    # lists as a BYOK provider). The per-tier image cap still applies.
    usage_tracker.check_model_access(body, tier, routed=managed_routing)

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

    # Safety net: the CQ hook fills the {{context_quilt}} placeholder ONLY
    # when CQ is enabled AND recall returned content. In every other path —
    # recall empty, teaser tier, CQ-disabled tier (hook skipped entirely
    # above), or context_quilt flag off — a literal {{context_quilt}} left in
    # the client's template would otherwise reach the model verbatim. Strip
    # any leftover unconditionally so a client can safely leave the literal
    # placeholder in and never leak it. Only the GP-owned slot is touched;
    # client-owned placeholders are left alone.
    if body.system_prompt and "{{context_quilt}}" in body.system_prompt:
        body = body.model_copy(update={
            "system_prompt": body.system_prompt.replace("{{context_quilt}}", "")
        })

    # 2.8. Locale injection — append the language directive to the now-final
    # system prompt so the model answers in the user's language. Central + in
    # one place so the rule can't drift across managed calls; no-op for
    # en/missing. Runs after assembly, sanitization, and feature hooks (the
    # final prompt) and before the stream branch, so it covers every path and
    # both prompt origins (GP-assembled and client-sent during migration).
    # See app.services.locale_injection + docs/handoffs/tr-managed-prompts-and-locale.md.
    from app.services.locale_injection import (
        apply as _apply_locale,
        normalize_locale as _norm_locale,
    )
    _output_locale = None  # set to the resolved locale only when injection fired
    _localized_system = _apply_locale(body.system_prompt, body.get_meta("locale"))
    if _localized_system != body.system_prompt:
        body = body.model_copy(update={"system_prompt": _localized_system})
        _output_locale = _norm_locale(body.get_meta("locale"))

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

    # Context cap (Project Chat only). Source of truth is
    # client-config.{locale}.json's `limits.project_chat.max_input_chars`,
    # locale-resolved off Accept-Language. iOS reads the same file via
    # /v1/config/client-config so its gauge denominator and our 413
    # threshold stay in lockstep — including locale-aware tightening
    # for CJK content where the char/4 token heuristic underestimates.
    # Falls back to tier.max_input_tokens × 4 (yaml) when client-config
    # is absent so English behavior matches the pre-cutover defaults.
    from app.routers.config import _parse_accept_language
    from app.services.client_config import project_chat_max_input_chars
    locale = _parse_accept_language(request.headers.get("Accept-Language"))
    fallback_chars = (
        tier.max_input_tokens * 4 if tier.max_input_tokens != -1 else -1
    )
    cap_chars = project_chat_max_input_chars(
        request.app.state.remote_configs,
        user.effective_tier,
        locale=locale,
        fallback_chars=fallback_chars,
    )
    actual_chars = len(assembled_prompt)
    if (
        is_project_chat_pre
        and cap_chars is not None
        and cap_chars != -1
        and actual_chars > cap_chars
    ):
        raise HTTPException(
            status_code=413,
            detail={
                "code": "context_too_large",
                "message": (
                    f"Selected context is too large for your tier "
                    f"({actual_chars} chars, max {cap_chars}). "
                    f"Deselect meetings or drop transcript chips."
                ),
                "feature_state": {
                    "feature": "project_chat",
                    "cta": {
                        "kind": "context_too_large",
                        "text": (
                            f"Selected context is {actual_chars // 1000}K chars, "
                            f"over your {cap_chars // 1000}K-char limit. "
                            f"Deselect meetings or drop transcripts to fit."
                        ),
                        "action": "trim_context",
                    },
                    "details": {
                        "max_chars": cap_chars,
                        "actual_chars": actual_chars,
                        "locale": locale or "en",
                        "tokenizer": "chars_direct",
                    },
                },
            },
        )

    # Budget gate — handles BOTH "already past cap" AND "this call would
    # push past cap" with a unified 200 + CTA envelope. Skips ONLY when:
    #   - limit is unlimited (Plus/Pro/Admin)
    #   - pricing data isn't loaded for the "would push over" case (fail
    #     open). The "already past cap" check still fires because it
    #     doesn't need a cost estimate.
    #
    # No call_type exemptions — every LLM call gates. Background pipelines
    # (AutoSummary, DeltaSummary, SummaryConsolidation, PostSessionAnalysis)
    # all gate too. iOS is the primary "don't start the meeting if over
    # cap" UX; GP is defense-in-depth so a hacked or stale client can't
    # bypass billing by routing through summary endpoints.
    if effective_limit != -1:
        already_exhausted = monthly_used >= effective_limit
        would_exceed = False
        if not already_exhausted and pricing.is_loaded:
            estimated_cost = estimate_call_cost_usd(
                pricing,
                provider=body.provider,
                model=body.model,
                input_tokens=estimated_input_tokens,
                max_output_tokens=body.max_tokens,
            )
            if estimated_cost is not None:
                would_exceed = would_exceed_budget(
                    monthly_used_usd=monthly_used,
                    estimated_cost_usd=estimated_cost,
                    effective_limit_usd=effective_limit,
                )
        if already_exhausted or would_exceed:
            credits_total = dollars_to_credits(effective_limit)
            credits_used = dollars_to_credits(monthly_used)
            credits_remaining = max(0, credits_total - credits_used)
            from app.routers.config import _parse_accept_language
            from app.services.budget_cta import get_budget_exhausted_cta
            _budget_locale = _parse_accept_language(request.headers.get("Accept-Language"))
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
                    "cta": get_budget_exhausted_cta(
                        request.app.state.remote_configs,
                        user.effective_tier,
                        _budget_locale,
                    ),
                },
            }
            return JSONResponse(status_code=200, content=block_payload)

    # 5.6b. Tech Rehearsal per-app budget gate. TR free/paid is the
    # X-TR-Entitlement header, independent of the SS tier above, and TR shares
    # the SS user row — so it can't use the per-user bucket. Cap TR spend per
    # UTC month by summing this user's techrehearsal usage_log rows. DORMANT
    # until apps.techrehearsal.budget.enabled is true (and the marginal-cost
    # estimate fails open when pricing/model isn't resolvable — the
    # already-over-cap check still fires).
    if app_id == "techrehearsal":
        from app.routers.config import load_apps
        from app.services import tr_budget
        _tr_budget_cfg = tr_budget.tr_budget_config(load_apps())
        if _tr_budget_cfg and _tr_budget_cfg.get("enabled"):
            _entitlement = request.headers.get("X-TR-Entitlement")
            _tr_estimate = None
            if pricing.is_loaded:
                _tr_estimate = estimate_call_cost_usd(
                    pricing,
                    provider=body.provider,
                    model=body.model,
                    input_tokens=estimated_input_tokens,
                    max_output_tokens=body.max_tokens,
                )
            _tr_block, _tr_info = await tr_budget.would_exceed_tr_budget(
                db, user.id, _entitlement, _tr_estimate, _tr_budget_cfg,
            )
            if _tr_block:
                logger.info(
                    "tr_budget_block user=%s entitlement=%s spent=%.4f cap=%.2f",
                    user.id, _tr_info["entitlement"], _tr_info["spent"], _tr_info["cap"],
                )
                # Lean over-cap envelope: HTTP 200 (not an error, so it won't
                # trip the client's error / on-device path), empty text, and a
                # single budget_exhausted flag to branch on. No credits/CTA —
                # the client renders its own limit-reached state. Confirm the
                # shape with TR before flipping `enabled` on.
                return JSONResponse(status_code=200, content={
                    "text": "",
                    "model": body.model,
                    "provider": body.provider,
                    "ai_tier": _tier_to_ai_tier_lazy(user.effective_tier),
                    "feature_state": {
                        "feature": "chat",
                        "app": "techrehearsal",
                        "entitlement": _tr_info["entitlement"],
                        "budget_exhausted": True,
                    },
                })

    # 5.7. Search gate. Four outcomes:
    #
    #   - Non-Anthropic provider with search_enabled=true → silently
    #     no-op the flag. Anthropic's web_search tool is the only
    #     provider-side mechanism we wire today; OpenAI/Gemini/Generic
    #     adapters ignore the flag. iOS-side enforcement (toggle only
    #     enabled when SS AI selected) is the primary layer; this is a
    #     server-side backstop so the counter doesn't increment for a
    #     search that physically can't run.
    #   - Free user with search_enabled=true → return 200 + paywall CTA,
    #     no LLM call (mirrors the budget-exhausted envelope).
    #   - Plus/Pro past hard cap → strip search_enabled before the LLM
    #     call (so AnthropicAdapter doesn't attach the tool), proceed
    #     with the query, return search_state.cta as a sidecar so iOS
    #     can show the "limit reached" notice.
    #   - Pro past soft cap → keep search_enabled, return soft-warning
    #     CTA as a sidecar.
    #
    # We deliberately gate AFTER the budget gate so a budget-exhausted
    # user gets one consistent reason rather than seeing search CTA on
    # top of a budget block.
    from app.services.search_caps import format_cta, get_search_caps
    search_state: dict | None = None
    if body.get_meta("search_enabled"):
        # Provider guard: only Anthropic actually supports web_search
        # today. Strip the flag immediately for any other provider so
        # the gate doesn't count a search that can't physically happen.
        if body.provider != "anthropic":
            if body.metadata is None:
                body.metadata = {}
            body.metadata["search_enabled"] = False
            # Surface a search_state sidecar with the tier's
            # `cta_unavailable` template so iOS can dispatch on
            # `cta.kind == "search_unavailable_for_provider"` the same
            # way it dispatches on `search_cap_exhausted`. Closes the
            # silent-strip gap that caused stuck-counter reports during
            # SS smoke testing (response field was absent → iOS had no
            # UX anchor for "search wasn't run"). Counters left null
            # because they're not load-bearing here; iOS reads
            # used/total from /v1/usage/me on app foreground.
            unavailable_caps = get_search_caps(
                request.app.state.remote_configs,
                user.effective_tier,
                locale=locale,
            )
            search_state = {
                "used": None,
                "total": None,
                "resets_at": None,
                "cta": format_cta(
                    unavailable_caps.cta_unavailable,
                    used=0,
                    total=unavailable_caps.searches_per_month,
                ) if unavailable_caps.cta_unavailable else None,
            }
            # Defensive log: paid-tier users with a search entitlement
            # should never end up here. If they do, model-routing has
            # drifted (e.g., a future config change pointed Pro `query`
            # at a non-Anthropic provider). Log so we catch it before
            # it becomes a stuck-counter ticket.
            if user.effective_tier in ("plus", "pro"):
                logger.warning(
                    "paid_tier_search_non_anthropic_provider tier=%s "
                    "provider=%s model=%s — model-routing may have "
                    "drifted; search entitlement is unusable",
                    user.effective_tier, body.provider, body.model,
                )
        else:
            search_caps_locale = locale  # already resolved for project-chat cap above
            caps = get_search_caps(
                request.app.state.remote_configs,
                user.effective_tier,
                locale=search_caps_locale,
            )
            # Read live counter from DB rather than the cached UserRecord —
            # users record is loaded once at request start, but lazy-reset
            # in check_quota may have just zeroed it.
            cursor = await db.execute(
                "SELECT searches_used FROM users WHERE id = ?",
                (user.id,),
            )
            row = await cursor.fetchone()
            searches_used = int(row["searches_used"] or 0) if row else 0

            if caps.searches_per_month == 0:
                # Free or any tier where search isn't provisioned. Hard reject
                # before the LLM call — same envelope shape as budget_gate so
                # iOS's existing CTA renderer just works. cta_only=true so
                # iOS can dispatch on the flag instead of branching on
                # text === "" (avoids the empty-bubble class of bug).
                cta = format_cta(
                    caps.cta_hard_cap,
                    used=searches_used,
                    total=caps.searches_per_month,
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "text": "",
                        "model": body.model,
                        "provider": body.provider,
                        "ai_tier": _tier_to_ai_tier_lazy(user.effective_tier),
                        "cta_only": True,
                        "feature_state": {
                            "feature": "search",
                            "cta": cta,
                        },
                    },
                )

            if searches_used >= caps.searches_per_month:
                # Past hard cap. Strip the flag before the adapter sees it
                # so no tool gets attached. Query proceeds, sidecar CTA on
                # the response tells iOS "we ran your query, but search was
                # off because you hit your monthly limit."
                if body.metadata is None:
                    body.metadata = {}
                body.metadata["search_enabled"] = False
                search_state = {
                    "used": searches_used,
                    "total": caps.searches_per_month,
                    "resets_at": user.allocation_resets_at,
                    "cta": format_cta(
                        caps.cta_hard_cap,
                        used=searches_used,
                        total=caps.searches_per_month,
                    ),
                }
            elif (
                caps.searches_soft_threshold is not None
                and searches_used >= caps.searches_soft_threshold
            ):
                # Past soft cap. Search still runs, but we surface a gentle
                # "approaching limit" notice so the user isn't surprised
                # when they hit the hard cap later.
                search_state = {
                    "used": searches_used,
                    "total": caps.searches_per_month,
                    "resets_at": user.allocation_resets_at,
                    "cta": format_cta(
                        caps.cta_soft_cap,
                        used=searches_used,
                        total=caps.searches_per_month,
                    ),
                }
            else:
                # Under all caps. Surface the counter so iOS can render an
                # "N of M used" pill if it wants — no CTA.
                search_state = {
                    "used": searches_used,
                    "total": caps.searches_per_month,
                    "resets_at": user.allocation_resets_at,
                    "cta": None,
                }

    # 5.8. Search-tool nudge. When `search_enabled` survives the gate
    # (Pro under hard cap; Plus under hard cap; Pro past soft cap), append
    # a one-sentence note to system_prompt so the model knows the tool is
    # available and when to reach for it. Without this, heavy in-context
    # prompts (ProjectChat injects 3+ meeting summaries) anchor Haiku to
    # the provided content and it never invokes web_search even when the
    # user explicitly asks about current/external info — observed on a
    # Pro ProjectChat send 2026-05-07 (request 58065b9d104f).
    #
    # Skipped when the gate stripped the flag (non-Anthropic provider,
    # Free, hard-cap) — no point hinting at a tool the adapter won't
    # attach.
    if body.get_meta("search_enabled"):
        body = body.model_copy(update={
            "system_prompt": body.system_prompt + (
                "\n\nYou have access to a web_search tool. Use it when the "
                "user asks about current events, recent news, or topics that "
                "aren't covered by the provided context."
            ),
        })

    # 5.94. Non-anthropic lanes render user_content only — fold
    # reference_text in so BYOK/pinned sends never lose chip content
    # (anthropic renders it as its own cached part in the adapter).
    if body.reference_text and body.provider != "anthropic":
        body = body.model_copy(update={
            "user_content": body.reference_text + "\n\n" + body.user_content,
            "reference_text": None,
        })

    # 5.95. Documents passthrough (#359). Runs AFTER the context-cap gate on
    # purpose: documents ride outside the char gauge (images precedent), so
    # server-side extraction text must not retroactively trip the 413 the
    # client's gauge never saw. Splits each attachment between native
    # passthrough (managed Pro on Anthropic, PDF) and server-side extraction
    # inlined into user_content — a downgrade, never an error or client retry.
    if body.documents:
        from app.services.documents import process_documents
        body = await process_documents(
            body,
            remote_configs=request.app.state.remote_configs,
            tier_name=user.effective_tier,
            managed_routing=managed_routing,
            user_identity={x for x in (user.id, user.email) if x},
        )

    # 5.96. Document generation is SERVER-armed only — a client-sent
    # generation flag never survives (it would bypass the gate and arm the
    # sandbox on any request). The non-streaming path re-arms below when
    # the gate passes.
    if body.generation:
        body = body.model_copy(update={"generation": False})

    # 5.97. Document generation arming (phase 2a). Gate mirrors documents
    # passthrough (config-enabled+tier, or allowed_users for e2e); surfaces
    # are the chat modes; managed anthropic only. When armed, the adapter
    # attaches the sandbox + document skills and this path collects the
    # artifacts after the response.
    from app.services.document_generation import generation_gate
    _gen_armed = generation_gate(
        remote_configs=request.app.state.remote_configs,
        tier_name=user.effective_tier,
        managed_routing=managed_routing,
        provider=body.provider,
        prompt_mode=body.get_meta("prompt_mode"),
        user_identity={x for x in (user.id, user.email) if x},
    )
    # Remember raw gate state before confirmation logic flips arming off:
    # capable-but-unarmed turns get the capability line below.
    _gen_capable = _gen_armed
    # 5.975. Confirmation envelope (handoff Part 1). While confirmation is
    # dark the arming rule stays gate-based (today's e2e lane). Once live:
    # a confirmed resend arms; an unconfirmed file intent gets the offer
    # envelope BEFORE any provider work (the budget gate already passed
    # above, so a blocked user sees the budget CTA, never the offer);
    # everything else is a normal chat turn — arming is confirmed-only.
    _gen_expected_seconds = None
    _gen_confirmation_enabled = False
    _template_id = None
    _gen_teaser_text = ""
    _gen_teaser_offer_id = None
    if _gen_armed:
        from app.routers.config import _parse_accept_language
        from app.services.document_generation import (
            build_offer_envelope,
            classify_generation_intent,
            load_generation_config,
        )
        _confirmation = load_generation_config(
            request.app.state.remote_configs,
            locale=_parse_accept_language(request.headers.get("Accept-Language")),
        )["confirmation"]
        _gen_expected_seconds = int(_confirmation["expected_seconds"])
        _gen_confirmation_enabled = bool(_confirmation["enabled"])
        if _confirmation["enabled"] and body.get_meta("generation_confirmed"):
            # Pill tap at a teaser (SS sends the offer_id echo AND the
            # generation_confirmed resend together, 2026-07-14): confirmed
            # skips the reply-interpret block below, so spend the echoed
            # offer here and inherit what it carries. Without this a tap at
            # a template-matched teaser rides the sandbox lane while a
            # typed yes at the SAME teaser rides the template lane, and the
            # originating ask content is lost.
            from app.services import generation_offers
            _offer_echo = body.get_meta("offer_id")
            _offer = (generation_offers.take(user.id, _offer_echo)
                      if _offer_echo else None)
            logger.info(
                "generation_offer_confirmed_check echo=%s hit=%s",
                "present" if _offer_echo else "absent", _offer is not None)
            if _offer is not None:
                _template_id = _offer.get("template_id")
                if _offer.get("ask_content"):
                    body = body.model_copy(update={
                        "user_content": _offer["ask_content"]
                        + "\n\nThe user confirmed the file build."})
        if _confirmation["enabled"] and not body.get_meta("generation_confirmed"):
            _gen_armed = False
            _meter = lambda creq, cresp, cms: usage_tracker.record_and_log(  # noqa: E731
                db, user=user, tier=tier, app_id=app_id,
                request=creq, response=cresp, elapsed_ms=cms, pricing=pricing,
            )
            # Conversational confirmation (handoff Part 1 v2): a send echoing
            # a live offer_id is the user's REPLY to our offer. GP judges it
            # server-side — a yes (including yes-with-revised-format) arms
            # generation on THIS very turn; anything else is a normal chat
            # turn. The offer dies either way (one-reply lifetime).
            from app.services import generation_offers
            _offer_echo = body.get_meta("offer_id")
            _offer = generation_offers.take(user.id, _offer_echo) if _offer_echo else None
            # Observability (2026-07-13 meeting-chat echo incident): metadata
            # KEY NAMES only — values can carry user content. This is the
            # wire evidence for "did the client echo at all / did the echo
            # miss the store" the next time a reply falls through.
            logger.info(
                "generation_offer_reply_check echo=%s hit=%s meta_keys=%s",
                "present" if _offer_echo else "absent",
                _offer is not None,
                sorted((body.metadata or {}).keys()),
            )
            if _offer is not None:
                from app.services.document_generation import interpret_offer_reply
                # SS's next build sends the user's verbatim reply as
                # metadata.reply_text — preferred over marker isolation of
                # the assembled user_content (which stays as the fallback
                # for older clients).
                _reply_verbatim = body.get_meta("reply_text")
                _reply = await interpret_offer_reply(
                    provider_router, _offer,
                    _reply_verbatim if _reply_verbatim else body.user_content,
                    verbatim=bool(_reply_verbatim), on_subcall=_meter)
                if _reply["confirm"]:
                    _gen_armed = True
                    _template_id = _offer.get("template_id")
                    _meta = dict(body.metadata or {})
                    _meta["generation_confirmed"] = True  # transport + rescue reuse
                    _updates: dict = {"metadata": _meta}
                    # The confirmed turn runs against the ORIGINATING ask's
                    # content (stored on the offer): reply sends assemble
                    # chat history only, and both lanes — extraction and
                    # sandbox — need the meeting content the user asked
                    # about, plus the reply for any revisions.
                    if _offer.get("ask_content"):
                        _reply_line = (body.get_meta("reply_text")
                                       or body.user_content[-500:])
                        _updates["user_content"] = (
                            _offer["ask_content"]
                            + "\n\nThe user confirmed the file build with: "
                            + str(_reply_line)[:500])
                    body = body.model_copy(update=_updates)
                    # the routing dial resolved before we knew this was a
                    # generation turn — re-resolve so it rides the first-send
                    # lane (same coherence rule as button-confirmed turns).
                    # Routing rows are "provider/model" strings: split exactly
                    # like the first-pass resolution, or the provider receives
                    # a vendor-prefixed id it rejects (first live chat-confirm
                    # failed on 'anthropic/claude-sonnet-4-6').
                    _re_model = _resolve_model_routing(
                        request, body, tier, effective_tier_name)
                    if _re_model:
                        _parts = _re_model.split("/", 1)
                        if len(_parts) == 2:
                            body = body.model_copy(update={
                                "provider": _parts[0], "model": _parts[1]})
                        elif _re_model != body.model:
                            body = body.model_copy(update={"model": _re_model})
            if not _gen_armed:
                # guaranteed catch first (deterministic, no LLM); the
                # classifier only judges the softer phrasings
                from app.services.document_generation import (
                    _question_portion,
                    explicit_file_ask,
                    looks_like_file_ask,
                )
                _intent = explicit_file_ask(body.user_content)
                if _intent is None:
                    _intent = await classify_generation_intent(
                        provider_router, body.user_content, on_subcall=_meter,
                    )
                # teaser candidate: file vocabulary present but no request
                # judged — the answer carries a served "want this as a real
                # file?" CTA; the tap resends with generation_confirmed
                # (SS renders the envelope family already). Judge the
                # QUESTION PORTION like every other intent check (#420):
                # the assembled tail of a file-heavy conversation always
                # carries file vocabulary, and scanning it teased a bare
                # "Test" follow-up (live 2026-07-14, post-generation chat).
                if ((_intent is None or not _intent.get("file_request"))
                        and looks_like_file_ask(_question_portion(body.user_content))):
                    _gen_teaser_text = str(_confirmation.get("teaser_text") or "")
                    # Teasers mint an offer too (joint call w/ SS
                    # 2026-07-14): a typed "yes" then rides the same
                    # echo → interpret → arm plumbing as real offers,
                    # while the pill tap keeps the generation_confirmed
                    # resend. Same one-reply lifetime and TTL.
                    from app.services.doc_templates import match_template as _mt
                    _gen_teaser_offer_id = generation_offers.create(
                        user.id,
                        (_intent or {}).get("format") or "xlsx",
                        (_intent or {}).get("gist") or "",
                        template_id=_mt(body.user_content),
                        ask_content=body.user_content or "")
                if _intent and _intent.get("file_request"):
                    from app.services.doc_templates import TEMPLATES, match_template
                    _tmpl = match_template(body.user_content)
                    _offer_id = generation_offers.create(
                        user.id, _intent.get("format") or "xlsx",
                        _intent.get("gist") or "", template_id=_tmpl,
                        ask_content=body.user_content or "")
                    _envelope = build_offer_envelope(
                        _confirmation, _intent.get("format"),
                        gist=_intent.get("gist") or "", offer_id=_offer_id)
                    if _tmpl:
                        # registry interception: propose the optimized build,
                        # keep the custom door open (a custom description
                        # falls through as normal chat and re-offers).
                        # en-only v1; served copy when templates localize.
                        _t = TEMPLATES[_tmpl]
                        _cta = _envelope["feature_state"]["cta"]
                        _cta["text"] = (
                            f"Sounds like you want a project timeline"
                            f"{(' ' + _intent.get('gist')) if _intent.get('gist') else ''}. "
                            f"I can build {_t['offer_noun']} in about "
                            f"{_t['expected_seconds']} seconds — or describe "
                            f"exactly what you have in mind and I'll build "
                            f"that custom instead. Want the polished one?")
                        _cta["details"]["template_id"] = _tmpl
                        _cta["details"]["expected_seconds"] = _t["expected_seconds"]
                    logger.info(
                        "generation_offer_served offer_id=%s surface=%s "
                        "template=%s format=%s stream_requested=%s",
                        _offer_id, body.get_meta("prompt_mode"), _tmpl,
                        _intent.get("format"), bool(body.stream),
                    )
                    return JSONResponse(content=_envelope)

    if _gen_armed and not _template_id:
        body = body.model_copy(update={"generation": True})
    elif _gen_armed and _template_id:
        # Template lane: no sandbox — the model's whole job is emitting the
        # plan as JSON; the registry's deterministic renderer draws the file.
        # The client-assembled system prompt CARRIES the project context
        # (meeting summaries — 7K chars on live Project Chat turns), so the
        # extraction directive APPENDS as an override instead of replacing
        # it: the first two live runs replaced it and the model, seeing no
        # meetings, asked the user to paste their plan.
        from app.services.doc_templates import TEMPLATES
        _t = TEMPLATES[_template_id]
        _client_sys = (body.system_prompt or "").strip()
        _extraction_sys = (
            _client_sys + "\n\n--- FILE BUILD OVERRIDE ---\n"
            + _t["extraction_prompt"]
            + " Ignore all earlier instructions about tone, style, or answer "
              "formatting — output only the JSON object. Never produce HTML "
              "or any visual rendering: the file is drawn separately from "
              "your JSON."
        ) if _client_sys else _t["extraction_prompt"]
        body = body.model_copy(update={
            "system_prompt": _extraction_sys,
            "temperature": 0.2,
            "max_tokens": 8000,
        })
        _gen_expected_seconds = _t["expected_seconds"]
    elif _gen_capable:
        # Capability line for gate-passing UNARMED turns. The client-
        # assembled prompt knows nothing about the file feature, so the
        # model's stock self-knowledge takes over — live 2026-07-14 it
        # told a Pro user "I don't have the ability to generate or
        # deliver actual downloadable files" three bubbles under a file
        # it built. Gate state is per-turn server knowledge (tier +
        # routing + surface), which is why this rides here and not in
        # the client prompt: a static client line would lie to
        # Free/BYOK users.
        _cap_line = (
            "FILE CAPABILITY: this product builds and delivers real "
            "downloadable files (Excel, Word, PowerPoint, PDF). When the "
            "user asks for a file, the platform detects it and handles "
            "the build. Never claim you cannot create or deliver files; "
            "answer naturally, and if the user wants a file, tell them "
            "to ask for it directly.")
        _sys = (body.system_prompt or "").rstrip()
        body = body.model_copy(update={
            "system_prompt": (_sys + "\n\n" + _cap_line) if _sys else _cap_line,
        })

    # 6. Stream or non-stream based on request + call_type
    # Only stream interactive queries; background tasks (summary, analysis) get full JSON.
    # Project Chat is also forced non-streaming so feature_state can land
    # cleanly in the JSON body (SSE injection of structured trailer fields
    # would require a separate event type and client-side merge).
    call_type = body.get_meta("call_type")
    is_project_chat = body.get_meta("prompt_mode") == "ProjectChat"
    # Meeting Chat parity (2026-07-12): the gate + confirmation machinery now
    # runs BEFORE this branch, so a streaming surface can draw an offer (a
    # single JSON on the SSE request — the same shape Project Chat clients
    # already handle) and an armed turn diverts to the generation transport
    # instead of the token stream. What admitted Project Chat all along was
    # its forced-non-stream route, not the stream flag — reconciled with SS.
    # Template-armed turns (confirmed registry offers) must divert exactly
    # like sandbox-armed ones: the model's whole output is the extraction
    # JSON, and only the non-streaming path runs the renderer. Live bug
    # 2026-07-13 18:54Z — a confirmed Gantt turn on meeting chat token-
    # streamed the raw task-graph JSON into the chat bubble and no file
    # was ever built (Project Chat was immune via its forced-non-stream
    # route, which is why every template test read green).
    should_stream = (
        body.stream
        and call_type not in ("summary", "analysis")
        and not is_project_chat
        and not body.generation
        and not _template_id
    )

    if should_stream:
        return await _handle_stream(
            body, request, user, db, provider_router, usage_tracker,
            pricing, tier, feature_hooks, hook_results,
            monthly_used, overage_balance, effective_limit,
            search_state,
        )

    # --- Non-streaming path (original) ---

    # 5.98. Generation turn records (phase 2 rescue, handoff Part 4). When
    # an armed turn carries a client-minted generation_id: an already-
    # terminal id replays the stored result (no second sandbox bill), a
    # still-running id 409s with honest-progress fields, and a fresh id is
    # registered so GET /v1/generations/{id} can rescue the turn after a
    # mid-turn app death. Sends without the id keep today's behavior.
    _generation_id = body.get_meta("generation_id") if _gen_armed else None
    if _generation_id:
        from app.services import generation_turns

        _stored = await generation_turns.lookup_terminal(db, user.id, _generation_id)
        if _stored is not None and _stored["status"] == "done":
            return JSONResponse(content={
                "text": _stored["text"],
                "generated_files": _stored["generated_files"],
                "replayed": True,
            })
        if _stored is None and not generation_turns.begin(
                user.id, _generation_id,
                expected_seconds=_gen_expected_seconds or generation_turns.DEFAULT_EXPECTED_SECONDS):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "generation_in_progress",
                    **{k: v for k, v in generation_turns.running_info(
                        user.id, _generation_id).items() if k != "status"},
                },
            )
        # a stored "failed" falls through: the resend is the retry

    # 5.9. Transcript cleanup for analysis calls. When call_type=="analysis"
    # carries a transcript_source the cleanup module knows how to handle
    # (today: "ocr_captions") and the server flag is on, run an LLM cleanup
    # pass over body.user_content (the raw transcript) before the analysis
    # call. The cleaned text replaces user_content for the main call AND
    # gets surfaced on the response as `cleaned_transcript` so iOS persists
    # it to MeetingRecord.cleanedTranscript. Failures are silent: we fall
    # back to raw and omit the field.
    cleaned_for_response: str | None = None
    if call_type == "analysis":
        from app.services.transcript_cleanup import (
            clean_transcript as _run_cleanup,
            should_clean as _should_clean,
        )
        settings = request.app.state.settings
        # Gate on the REPORTED source only — never infer one. A null source
        # (older build / re-analysis) is left alone rather than assumed OCR, so
        # an audio transcript can't get OCR cleanup. App reports, GP decides.
        _ts = body.get_meta("transcript_source")
        if _should_clean(_ts, settings.captions_cleanup_enabled):
            from app.routers.config import _parse_accept_language
            _locale = _parse_accept_language(request.headers.get("Accept-Language"))
            _cleaned = await _run_cleanup(
                provider_router,
                body.user_content,
                request.app.state.remote_configs,
                _ts,
                locale=_locale,
                meeting_id=body.get_meta("meeting_id"),
                # Meter the cleanup as its own usage_log row + cost, so this
                # second LLM call shows in the Query Log and counts toward
                # budget instead of running invisibly.
                on_subcall=lambda creq, cresp, cms: usage_tracker.record_and_log(
                    db, user=user, tier=tier, app_id=app_id,
                    request=creq, response=cresp, elapsed_ms=cms, pricing=pricing,
                ),
            )
            if _cleaned:
                body = body.model_copy(update={"user_content": _cleaned})
                cleaned_for_response = _cleaned

    # Steps 6-10 (route -> collect -> meter -> assemble) run inside a
    # closure so the generation transport (Phase A) can drive the same
    # pipeline behind SSE heartbeats. The JSON path awaits it directly —
    # identical behavior, one indentation level down.
    async def _run_turn_tail(db=db) -> JSONResponse:
        # `db` is a PARAMETER (defaulting to the request-scoped connection):
        # the SSE transport runs this closure while the response streams —
        # AFTER dependency teardown closes the request's connection — so it
        # passes its own. `body` is rebound below (generated-count metadata);
        # without the nonlocal the first read would raise UnboundLocalError.
        nonlocal body
        # 6. Route to provider
        start = time.monotonic()
        try:
            from app.services.anthropic_or_fallback import route_with_fallback
            response = await route_with_fallback(
                provider_router, body, db, request.app.state.settings,
            )
        except HTTPException as _route_exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            await usage_tracker.log_usage(
                db, user.id, body, None, elapsed_ms, status="error", app_id=app_id
            )
            if _generation_id:
                from app.services import generation_turns
                _detail = _route_exc.detail if isinstance(_route_exc.detail, dict) else {
                    "message": str(_route_exc.detail)}
                await generation_turns.finish(
                    db, user_id=user.id, app_id=app_id, generation_id=_generation_id,
                    status="failed", error=_detail,
                )
            raise
        except Exception:
            # Generation turns bypass the OR fallback, so raw transport errors
            # (e.g. ReadTimeout) can reach here — record the failure so the
            # rescue lookup answers honestly, then re-raise unchanged.
            if _generation_id:
                from app.services import generation_turns
                await generation_turns.finish(
                    db, user_id=user.id, app_id=app_id, generation_id=_generation_id,
                    status="failed", error={"code": "provider_error",
                                            "message": "generation leg failed"},
                )
            raise

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 6.5. Unwrap a stray ```json code fence the model wrapped around a JSON
        # response (managed JSON call types say "no code fences" but models still
        # do it). Safe no-op for prose/markdown answers. Non-stream path only;
        # the JSON call types don't stream.
        if response and response.text:
            response.text = _strip_json_code_fence(response.text)

        # 6.7. Collect generated artifacts (phase 2a) — best-effort, never
        # blocks the text answer. Staged rows ride the response as
        # `generated_files`; count/bytes land in usage metering below.
        generated_payload: list[dict] = []
        if _template_id and response and response.text:
            # Template lane execution: parse the extraction, render
            # deterministically, stage through the same pipeline as sandbox
            # artifacts. Failures fall back to the raw model text so the
            # user never gets a dead turn.
            try:
                from app.services import generated_files as _staging
                from app.services.doc_templates import TEMPLATES, parse_extraction
                _t = TEMPLATES[_template_id]
                _plan = parse_extraction(response.text)
                _bytes = await asyncio.to_thread(_t["renderer"], _plan)
                _row = await _staging.stage(
                    db, user_id=user.id, app_id=app_id,
                    name=_t["filename"], media_type=_t["media_type"], content=_bytes)
                if _row:
                    generated_payload = [_row]
                    _n = len(_plan.get("tasks") or [])
                    response.text = (f"Built your {_t['format']} — "
                                     f"{_n} tasks and milestones from "
                                     f"{_plan.get('project') or 'the project'}.")
                    # metering stamp (the sandbox block below is skipped on
                    # the template lane — first live run staged+delivered
                    # but showed gen {} in usage metadata)
                    _gmeta = dict(body.metadata or {})
                    _gmeta["generated_count"] = 1
                    _gmeta["generated_bytes"] = _row["size_bytes"]
                    _gmeta["template_id"] = _template_id
                    body = body.model_copy(update={"metadata": _gmeta})
            except Exception:
                logger.exception("template lane render failed — serving raw text")
        if body.generation and response and response.raw_response_json:
            from app.services.document_generation import collect_generated_files
            generated_payload = await collect_generated_files(
                db,
                raw_response_json=response.raw_response_json,
                api_key=request.app.state.settings.anthropic_api_key,
                remote_configs=request.app.state.remote_configs,
                user_id=user.id,
                app_id=app_id,
            )
            if generated_payload:
                _gmeta = dict(body.metadata or {})
                _gmeta["generated_count"] = len(generated_payload)
                _gmeta["generated_bytes"] = sum(g["size_bytes"] for g in generated_payload)
                body = body.model_copy(update={"metadata": _gmeta})

        if _generation_id:
            from app.services import generation_turns
            await generation_turns.finish(
                db, user_id=user.id, app_id=app_id, generation_id=_generation_id,
                status="done", text=(response.text or ""),
                generated_files=generated_payload,
            )

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
        await usage_tracker.log_usage(db, user.id, body, response, elapsed_ms, app_id=app_id)

        # 9.1. Search-usage tracking: count search invocations Anthropic
        # actually performed (mirrored into usage["web_search_requests"] by
        # AnthropicAdapter), increment the per-user counter, and write an
        # audit row per search-bearing response. Fail-open: if the increment
        # fails (e.g., transient DB error), the user effectively gets the
        # search free for this request — accept it rather than block them.
        try:
            searches_performed = int(
                (response.usage or {}).get("web_search_requests") or 0
            )
        except (TypeError, ValueError):
            searches_performed = 0

        # Surface "did search actually run?" as an explicit boolean so iOS
        # can branch on it (rather than inferring from CTA presence). Fires
        # for every request that had search_state populated — present even
        # when the user is under all caps and gets a counter pill with no
        # CTA. False when the gate stripped the flag (hard cap) OR when the
        # provider doesn't support search.
        if search_state is not None:
            search_state["was_used"] = searches_performed > 0

        if searches_performed > 0:
            try:
                await db.execute(
                    "UPDATE users SET searches_used = searches_used + ? WHERE id = ?",
                    (searches_performed, user.id),
                )
                # Bump the on-the-wire counter to match the post-increment
                # DB state so iOS's `updateSearchUsage(from: search_state)`
                # advances the pill in lockstep with this response. Without
                # this, search_state.used is the pre-LLM snapshot and iOS
                # writes back the same value it already had — the pill
                # appears frozen until /v1/usage/me is fetched on app
                # foreground (observed bug, request 19:46:00 UTC 2026-05-07).
                if search_state is not None and search_state.get("used") is not None:
                    search_state["used"] = search_state["used"] + searches_performed
                # Audit row: one per response. Cost = $10 / 1000 searches
                # (Anthropic flat fee for web_search tool usage); the input
                # tokens consumed by returned search content are tracked
                # separately under usage_log.estimated_cost_usd.
                import uuid as _uuid
                await db.execute(
                    """INSERT INTO search_usage
                       (id, user_id, request_timestamp, meeting_id, provider,
                        model, searches_count, search_cost_usd)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(_uuid.uuid4()),
                        user.id,
                        datetime.now(timezone.utc).isoformat(),
                        body.get_meta("meeting_id"),
                        body.provider,
                        body.model,
                        searches_performed,
                        searches_performed * 0.01,
                    ),
                )
                await db.commit()
            except Exception as e:
                logger.warning(
                    "search_usage tracking failed for user %s: %s — "
                    "search ran, counter not incremented (fail-open)",
                    user.id, e,
                )

        # 9.5. Feature hooks (after LLM) — async, non-blocking
        for feature_name, hook in feature_hooks.items():
            state = tier.feature_state(feature_name)
            if feature_name in hook_results:
                await hook.after_llm(user, body, response, hook_results[feature_name], state)

        # Server-controlled tier label. Decoupled from `response.model` so we
        # can swap models per tier without breaking iOS attribution UI.
        from app.services.ai_tier import tier_to_ai_tier
        response.ai_tier = tier_to_ai_tier(effective_tier_name)

        # Surface the cleaned transcript (if cleanup ran for this analysis call)
        # so iOS can persist it to MeetingRecord.cleanedTranscript. Absent when
        # cleanup was skipped or failed — iOS falls back to raw silently.
        if cleaned_for_response is not None:
            response.cleaned_transcript = cleaned_for_response

        # 10. Build response with allocation headers
        response_data = response.model_dump()
        # Raw provider wire payloads are metering/debug internals (usage_log
        # metadata), never client wire: raw_request carries the assembled
        # system prompt (GP-owned for managed calls) and raw_response the
        # provider's identity and internal blocks — and together they tripled
        # response size (caught by SS's 74KB console forensics, 2026-07-11).
        response_data.pop("raw_request_json", None)
        response_data.pop("raw_response_json", None)

        # Search-state sidecar (independent of feature_state, which is owned
        # by the project_chat / budget paths). Always populated when the
        # request had search_enabled=true so iOS can render a "searches used
        # this month" pill, with `cta` non-null only when a soft- or hard-cap
        # message needs to surface.
        if search_state is not None:
            response_data["search_state"] = search_state

        # Generated artifacts (phase 2a): additive — absent when none. The
        # client downloads immediately (URLs are a 6h fetch window, not storage).
        if generated_payload:
            response_data["generated_files"] = generated_payload
        if _gen_teaser_text and not generated_payload:
            # soft-intent teaser (SS design, replacing their manual toggle):
            # same envelope family they render everywhere; the tap resends
            # this ask with generation_confirmed
            response_data["feature_state"] = {
                "feature": "document_generation",
                "state": "available",
                "cta": {
                    "kind": "generation_teaser",
                    "text": _gen_teaser_text,
                    "action": "confirm_generation",
                    # add-only: offer_id lets a TYPED yes ride the offer
                    # echo lane; the pill tap keeps generation_confirmed
                    "details": ({"offer_id": _gen_teaser_offer_id}
                                if _gen_teaser_offer_id else {}),
                },
            }

        json_response = JSONResponse(content=response_data)

        # Surface the output-language injection so a Spanish end-to-end run can be
        # confirmed at the wire (GP injected) vs the model merely happening to
        # answer in-language. Present only when the directive was actually applied.
        if _output_locale:
            json_response.headers["X-Output-Locale"] = _output_locale

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

    # 6b. Generation transport, Phase A (handoff Part 2). A CONFIRMED
    # generation turn is answered as SSE on every surface: started ->
    # timer-based heartbeats (honest: elapsed vs served expectation, no
    # fake precision) -> result carrying the byte-identical JSON body ->
    # or error. The client's only timeout is "no event for 30s". Unconfirmed
    # armed turns (the dark e2e lane) keep the plain JSON path.
    _gen_sse = bool(
        (body.generation or _template_id)
        and body.get_meta("generation_confirmed")
        and _gen_confirmation_enabled
    )
    if not _gen_sse:
        return await _run_turn_tail()

    import json as _json

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {_json.dumps(data)}\n\n"

    async def _generation_events():
        _t0 = time.monotonic()
        _expected = _gen_expected_seconds or 150
        yield _sse("generation_started", {
            "expected_seconds": _expected,
            "expected_format": body.get_meta("expected_format"),
        })
        # Own DB connection: this generator outlives the request scope (the
        # dependency-injected connection is already torn down while the
        # stream body runs — caught by CI, ValueError: no active connection).
        import aiosqlite as _aiosqlite
        _db_path = request.app.state.settings.database_url.replace(
            "sqlite+aiosqlite:///", "")
        _sse_db = await _aiosqlite.connect(_db_path)
        _sse_db.row_factory = _aiosqlite.Row

        async def _tail_owning_db():
            # The TASK owns the connection, not the generator: a client
            # disconnect cancels the generator, but the turn must run to
            # completion and write its rescue row + stage its artifact —
            # that's the entire paid-but-unseen case rescue exists for.
            try:
                return await _run_turn_tail(db=_sse_db)
            finally:
                await _sse_db.close()

        _task = asyncio.create_task(_tail_owning_db())
        while True:
            done, _ = await asyncio.wait({_task}, timeout=5)
            if done:
                break
            yield _sse("generation_progress", {
                "elapsed_seconds": int(time.monotonic() - _t0),
                "expected_seconds": _expected,
                "phase": "working",
            })
        try:
            _resp = _task.result()
            yield _sse("generation_result", _json.loads(bytes(_resp.body)))
        except HTTPException as e:
            _detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            _ev = {"code": _detail.get("code", "provider_error"),
                   "message": _detail.get("message", "generation failed")}
            if isinstance(_detail.get("details"), dict):
                _ev["details"] = _detail["details"]   # same typed family as HTTP errors
            yield _sse("generation_error", _ev)
        except Exception:
            logger.exception("generation transport: turn failed")
            yield _sse("generation_error", {"code": "provider_error",
                                            "message": "generation failed"})

    return StreamingResponse(_generation_events(), media_type="text/event-stream")


async def _handle_stream(
    body, request, user, db, provider_router, usage_tracker,
    pricing, tier, feature_hooks, hook_results,
    monthly_used, overage_balance, effective_limit,
    search_state: dict | None = None,
):
    """SSE streaming path for interactive chat queries.

    Streams text deltas as they arrive from the provider. Cost recording,
    usage logging, and after_llm hooks run after the stream completes.

    `search_state` (when non-None) is the gate's pre-LLM payload: counter,
    cap, optional CTA. It's emitted in the final `done` SSE event with
    `was_used` filled in from the streamed response's usage.

    Note: The generator opens its own DB connection because FastAPI's
    request-scoped Depends(get_db) closes before the generator finishes.
    """
    from app.database import get_db as _get_db
    # App identity (X-App-ID) for the per-app analytics tag on usage_log.
    # Captured by the nested event_stream() closure below.
    app_id = getattr(request.state, "app_id", "unknown")
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
        from app.services.anthropic_or_fallback import route_stream_with_fallback
        # Tracked across the heartbeat loop so it can be cancelled on any
        # exit path (see the finally below).
        pending = None
        try:
            async with asyncio.timeout(_CHAT_STREAM_WALL_CLOCK_SECONDS):
                # Consume the upstream iterator one event at a time via a
                # tracked task instead of `async for`, so a silent gap can
                # emit a heartbeat WITHOUT cancelling the in-flight read.
                # asyncio.wait (unlike wait_for) does not cancel the pending
                # task when its own timeout elapses — that's what makes this
                # safe to run against an async generator.
                agen = route_stream_with_fallback(
                    provider_router, body, db, request.app.state.settings,
                ).__aiter__()
                phase = "waiting"  # pre-first-token; flips to "generating"
                pending = asyncio.ensure_future(agen.__anext__())
                while True:
                    done_set, _ = await asyncio.wait(
                        {pending}, timeout=_STREAM_HEARTBEAT_SECONDS
                    )
                    if not done_set:
                        # No event within the heartbeat window — emit a
                        # liveness/phase ping; the read keeps running. Phase
                        # only, no fabricated fraction.
                        elapsed_ms = int((time.monotonic() - start) * 1000)
                        hb = json.dumps({
                            "type": "progress",
                            "phase": phase,
                            "elapsed_ms": elapsed_ms,
                        })
                        yield f"data: {hb}\n\n"
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        break
                    pending = asyncio.ensure_future(agen.__anext__())
                    if event.get("done"):
                        final_response = event.get("response")
                    else:
                        if phase == "waiting":
                            phase = "generating"
                        # Yield text delta as SSE
                        sse_data = json.dumps({"type": "text", "text": event["text"]})
                        yield f"data: {sse_data}\n\n"

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            async for err_db in _get_db():
                await usage_tracker.log_usage(
                    err_db, user.id, body, None, elapsed_ms, status="timeout", app_id=app_id
                )
            timeout_data = json.dumps({
                "type": "error",
                "code": "stream_timeout",
                "message": f"Stream exceeded {_CHAT_STREAM_WALL_CLOCK_SECONDS}s cap.",
            })
            yield f"data: {timeout_data}\n\n"
            return

        except HTTPException as exc:
            # Surface enough detail for iOS to show a useful message.
            # Without this, the SSE event was just `{"type":"error","text":"Provider error"}`
            # which iOS rendered as the unhelpful "Stream error: stream_error".
            elapsed_ms = int((time.monotonic() - start) * 1000)
            async for err_db in _get_db():
                await usage_tracker.log_usage(
                    err_db, user.id, body, None, elapsed_ms, status="error", app_id=app_id
                )

            detail = exc.detail
            if isinstance(detail, dict):
                msg = detail.get("message") or detail.get("detail") or "Provider error"
                code = detail.get("code") or f"upstream_{exc.status_code}"
            elif isinstance(detail, str) and detail:
                msg = detail
                code = f"upstream_{exc.status_code}"
            else:
                msg = "Provider error"
                code = f"upstream_{exc.status_code}"

            logger.warning(
                "stream_provider_error status=%s code=%s msg=%s",
                exc.status_code, code, str(msg)[:200],
            )

            error_data = json.dumps({
                "type": "error",
                "code": code,
                "http_status": exc.status_code,
                "text": msg,
            })
            yield f"data: {error_data}\n\n"
            return

        except Exception as exc:  # noqa: BLE001 — defense-in-depth for unexpected failures
            elapsed_ms = int((time.monotonic() - start) * 1000)
            async for err_db in _get_db():
                await usage_tracker.log_usage(
                    err_db, user.id, body, None, elapsed_ms, status="error", app_id=app_id
                )
            logger.exception("stream_unexpected_error")
            error_data = json.dumps({
                "type": "error",
                "code": "internal_error",
                "text": "Something went wrong on our side. Try again.",
            })
            yield f"data: {error_data}\n\n"
            return

        finally:
            # Cancel any in-flight upstream read so an abandoned stream
            # (wall-clock timeout, provider error, or client disconnect)
            # doesn't leak the task or hold the upstream connection open.
            if pending is not None and not pending.done():
                pending.cancel()

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

        # Mirror the non-streaming path: count search invocations from
        # usage, increment the per-user counter, write an audit row.
        # Fail-open on DB errors (search ran; user gets it free this turn).
        try:
            searches_performed = int(
                (final_response.usage if final_response else {} or {}).get(
                    "web_search_requests"
                ) or 0
            )
        except (TypeError, ValueError):
            searches_performed = 0

        # Fill in was_used so iOS can branch on whether search actually
        # ran for this stream (independent of CTA presence).
        if search_state is not None:
            search_state["was_used"] = searches_performed > 0

        async for stream_db in _get_db():
            await usage_tracker.record_cost(stream_db, user.id, request_cost, tier, user=user)
            await usage_tracker.log_usage(stream_db, user.id, body, final_response, elapsed_ms, app_id=app_id)

            if searches_performed > 0:
                try:
                    await stream_db.execute(
                        "UPDATE users SET searches_used = searches_used + ? WHERE id = ?",
                        (searches_performed, user.id),
                    )
                    # Mirror the non-streaming path: bump search_state.used
                    # so the SSE done event carries the post-increment count.
                    if (
                        search_state is not None
                        and search_state.get("used") is not None
                    ):
                        search_state["used"] = (
                            search_state["used"] + searches_performed
                        )
                    import uuid as _uuid
                    await stream_db.execute(
                        """INSERT INTO search_usage
                           (id, user_id, request_timestamp, meeting_id, provider,
                            model, searches_count, search_cost_usd)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(_uuid.uuid4()),
                            user.id,
                            datetime.now(timezone.utc).isoformat(),
                            body.get_meta("meeting_id"),
                            body.provider,
                            body.model,
                            searches_performed,
                            searches_performed * 0.01,
                        ),
                    )
                    await stream_db.commit()
                except Exception as e:
                    logger.warning(
                        "stream search_usage tracking failed for user %s: %s — "
                        "search ran, counter not incremented (fail-open)",
                        user.id, e,
                    )

            for feature_name, hook in feature_hooks.items():
                state = tier.feature_state(feature_name)
                if feature_name in hook_results:
                    await hook.after_llm(user, body, final_response, hook_results[feature_name], state)

        from app.services.ai_tier import tier_to_ai_tier

        # Final event with metadata (tokens, cost, allocation, search).
        # search_state is included so streaming Meeting Chat queries get
        # the same CTA + counter signal as the JSON path — without it,
        # an iOS streaming consumer would silently miss soft/hard-cap
        # CTAs on the very paths SS wired the inline pill into.
        done_data = {
            "type": "done",
            "input_tokens": final_response.input_tokens if final_response else None,
            "output_tokens": final_response.output_tokens if final_response else None,
            "cost": final_response.cost if final_response else None,
            "usage": final_response.usage if final_response else None,
            "ai_tier": tier_to_ai_tier(user.effective_tier),
        }
        if search_state is not None:
            done_data["search_state"] = search_state
        if effective_limit != -1:
            new_used = monthly_used + request_cost
            done_data["allocation_percent"] = min(100, new_used / effective_limit * 100)
        yield f"data: {json.dumps(done_data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )
