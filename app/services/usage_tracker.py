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
        """Raise 429 if daily token quota exceeded."""
        if tier.daily_token_limit == -1:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await db.execute(
            """SELECT COALESCE(SUM(COALESCE(input_tokens, 0)), 0)
                    + COALESCE(SUM(COALESCE(output_tokens, 0)), 0)
               FROM usage_log
               WHERE user_id = ? AND request_timestamp >= ? AND status = 'success'""",
            (user.id, today),
        )
        row = await cursor.fetchone()
        used = row[0] if row else 0

        if used >= tier.daily_token_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "quota_exceeded",
                    "message": (
                        f"Daily token quota exceeded "
                        f"({used}/{tier.daily_token_limit})"
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
        await db.execute(
            """INSERT INTO usage_log
               (id, user_id, provider, model, input_tokens, output_tokens,
                estimated_cost_usd, request_timestamp, response_time_ms,
                status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                user_id,
                request.provider,
                request.model,
                response.input_tokens if response else None,
                response.output_tokens if response else None,
                None,  # Cost estimation deferred to v0.2
                datetime.now(timezone.utc).isoformat(),
                response_time_ms,
                status,
                error_msg,
            ),
        )
        await db.commit()
