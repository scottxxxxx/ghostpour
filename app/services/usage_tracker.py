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
    ) -> None:
        """Raise 429 if daily token or cost quota exceeded."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check token limit
        if tier.daily_token_limit != -1:
            cursor = await db.execute(
                """SELECT COALESCE(SUM(COALESCE(input_tokens, 0)), 0)
                        + COALESCE(SUM(COALESCE(output_tokens, 0)), 0)
                   FROM usage_log
                   WHERE user_id = ? AND request_timestamp >= ?
                     AND status = 'success'""",
                (user.id, today),
            )
            row = await cursor.fetchone()
            used_tokens = row[0] if row else 0

            if used_tokens >= tier.daily_token_limit:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "quota_exceeded",
                        "message": (
                            f"Daily token quota exceeded "
                            f"({used_tokens}/{tier.daily_token_limit})"
                        ),
                    },
                )

        # Check cost limit
        if tier.daily_cost_limit_usd != -1:
            cursor = await db.execute(
                """SELECT COALESCE(SUM(COALESCE(estimated_cost_usd, 0)), 0)
                   FROM usage_log
                   WHERE user_id = ? AND request_timestamp >= ?
                     AND status = 'success'""",
                (user.id, today),
            )
            row = await cursor.fetchone()
            used_cost = row[0] if row else 0.0

            if used_cost >= tier.daily_cost_limit_usd:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "quota_exceeded",
                        "message": (
                            f"Daily cost quota exceeded "
                            f"(${used_cost:.4f}/${tier.daily_cost_limit_usd:.2f})"
                        ),
                    },
                )

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
