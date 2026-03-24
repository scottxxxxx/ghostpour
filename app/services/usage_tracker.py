import json
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse
from app.models.tier import TierDefinition
from app.models.user import UserRecord


class UsageTracker:
    def check_model_access(
        self,
        request: ChatRequest,
        tier: TierDefinition,
    ) -> None:
        """Raise 403 if provider or model not allowed for this tier."""
        if (
            "*" not in tier.allowed_providers
            and request.provider not in tier.allowed_providers
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden",
                    "message": (
                        f"Provider '{request.provider}' not available "
                        f"on {tier.display_name} tier"
                    ),
                },
            )

        if (
            "*" not in tier.allowed_models
            and request.model not in tier.allowed_models
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "model_not_allowed",
                    "message": (
                        f"Model '{request.model}' not available "
                        f"on {tier.display_name} tier"
                    ),
                },
            )

        if request.images:
            if len(request.images) > tier.max_images_per_request:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "forbidden",
                        "message": (
                            f"Max {tier.max_images_per_request} images allowed "
                            f"on {tier.display_name} tier"
                        ),
                    },
                )

    async def check_quota(
        self,
        db: aiosqlite.Connection,
        user: UserRecord,
        tier: TierDefinition,
    ) -> tuple[float, float]:
        """Check monthly allocation + overage. Returns (monthly_used, overage_balance).

        Raises 429 if both monthly allocation and overage are exhausted.
        Returns the current values so chat router can set response headers.
        """
        # Determine effective limit (trial cap overrides monthly limit)
        effective_limit = tier.monthly_cost_limit_usd
        if user.is_trial and tier.trial_cost_limit_usd is not None:
            effective_limit = tier.trial_cost_limit_usd

        if effective_limit == -1:
            return 0.0, 0.0  # Unlimited (admin)

        # Simulation: force allocation exhausted
        if user.simulated_exhausted:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "allocation_exhausted",
                    "message": (
                        f"Monthly allocation exhausted "
                        f"(${effective_limit:.2f}/${effective_limit:.2f}). "
                        f"Purchase overage credits or upgrade your plan."
                    ),
                    "details": {
                        "monthly_used": effective_limit,
                        "monthly_limit": effective_limit,
                        "overage_balance": 0.0,
                        "fallback": "on_device",
                        "simulated": True,
                    },
                },
            )

        # Read user's allocation state
        cursor = await db.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?",
            (user.id,),
        )
        row = await cursor.fetchone()
        monthly_used = float(row["monthly_used_usd"] or 0) if row else 0.0

        # Monthly allocation exhausted?
        if monthly_used >= effective_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "allocation_exhausted",
                    "message": (
                        f"Monthly allocation exhausted "
                        f"(${monthly_used:.4f}/${effective_limit:.2f}). "
                        f"Upgrade your plan for more hours."
                    ),
                    "details": {
                        "monthly_used": monthly_used,
                        "monthly_limit": effective_limit,
                        "fallback": "on_device",
                    },
                },
            )

        return monthly_used, 0.0

    async def record_cost(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        cost: float,
        tier: TierDefinition,
        user: UserRecord | None = None,
    ) -> None:
        """Deduct cost from monthly allocation or overage balance."""
        effective_limit = tier.monthly_cost_limit_usd
        if user and user.is_trial and tier.trial_cost_limit_usd is not None:
            effective_limit = tier.trial_cost_limit_usd

        if effective_limit == -1 or cost <= 0:
            return

        await db.execute(
            "UPDATE users SET monthly_used_usd = monthly_used_usd + ? WHERE id = ?",
            (cost, user_id),
        )
        await db.commit()

    async def log_usage(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        request: ChatRequest,
        response: ChatResponse | None,
        response_time_ms: int,
        status: str = "success",
        error_msg: str | None = None,
    ) -> None:
        # Build metadata from usage + cost dicts
        metadata: dict = {}
        if response and response.usage:
            metadata["usage"] = response.usage
        if response and response.cost:
            metadata["cost"] = response.cost

        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        # Extract estimated cost
        estimated_cost = None
        if response and response.cost:
            estimated_cost = response.cost.get("total_cost")

        # Extract cached tokens from usage metadata
        cached_tokens = None
        if response and response.usage:
            u = response.usage
            cached_tokens = (
                u.get("prompt_tokens_details.cached_tokens")  # OpenAI
                or u.get("cache_read_input_tokens")  # Anthropic
                or u.get("cachedContentTokenCount")  # Gemini
            )
            if cached_tokens is not None:
                cached_tokens = int(cached_tokens)

        await db.execute(
            """INSERT INTO usage_log
               (id, user_id, provider, model, input_tokens, output_tokens,
                estimated_cost_usd, request_timestamp, response_time_ms,
                status, error_message, call_type, prompt_mode,
                image_count, session_duration_sec, cached_tokens, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                user_id,
                request.provider,
                request.model,
                response.input_tokens if response else None,
                response.output_tokens if response else None,
                estimated_cost,
                datetime.now(timezone.utc).isoformat(),
                response_time_ms,
                status,
                error_msg,
                request.call_type,
                request.prompt_mode,
                request.image_count or (len(request.images) if request.images else 0),
                request.session_duration_sec,
                cached_tokens,
                metadata_json,
            ),
        )
        await db.commit()
