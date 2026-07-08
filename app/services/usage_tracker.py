import json
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse
from app.models.tier import TierDefinition
from app.models.user import UserRecord
from app.services.allocation_reset import lazy_reset_if_due


def _normalize_scenario(value: object) -> str | None:
    """Normalize a client-sent metadata.scenario into a stored tag.

    Tech Rehearsal is scenario-driven (interview / negotiation / personal …)
    under one app_id; the client tags each call so analytics can slice
    scenarios without splitting app_id. Vocabulary is intentionally open
    (extensible), so we don't whitelist — just trim, lowercase, and cap
    length to keep the column clean. Returns None when not a usable string.
    """
    if not isinstance(value, str):
        return None
    s = value.strip().lower()[:32]
    return s or None


def _normalize_scenario_kind(value: object) -> str | None:
    """Like _normalize_scenario but CASE-PRESERVING: scenario_kind is TR's
    camelCase ScenarioKind enum (jobInterview, payNegotiation, ...) and the
    stored value should match what prompt assembly branched on verbatim."""
    if not isinstance(value, str):
        return None
    s = value.strip()[:40]
    return s or None


class UsageTracker:
    def check_model_access(
        self,
        request: ChatRequest,
        tier: TierDefinition,
        routed: bool = False,
    ) -> None:
        """Raise 403 if provider/model/images not allowed for this tier.

        The provider and model allowlists are a BYOK guard: they gate what a
        user may explicitly pin. When ``routed`` is True the client sent
        model/provider=auto and GP's own router chose a tier-appropriate target
        (e.g. tr_research_company -> openrouter/perplexity/sonar), so those two
        allowlists do not apply — GP, not the user, picked the provider. The
        per-tier image cap below still applies in both cases.
        """
        if not routed:
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
        """Read current usage state. Returns (monthly_used, overage_balance).

        Previously raised 429 when monthly_used >= effective_limit. That path
        is gone — the budget gate (app/services/budget_gate.py) is now the
        sole authority for "you're over cap" responses, emitting the
        unified 200 + feature_state.cta envelope (or canned report on the
        meeting-report path). One wire shape, no legacy/new split.

        Still raises 429 for the simulated_exhausted testing path so the
        admin "force exhausted" toggle keeps working — that's a developer
        feature, not a real-user one, and the wire shape there doesn't
        need to match the production envelope.
        """
        # Determine effective limit (trial cap overrides monthly limit)
        effective_limit = tier.monthly_cost_limit_usd
        if user.is_trial and tier.trial_cost_limit_usd is not None:
            effective_limit = tier.trial_cost_limit_usd

        if effective_limit == -1:
            return 0.0, 0.0  # Unlimited (admin)

        # Lazy reset on first read past allocation_resets_at. Catches Free
        # users (no Apple webhook path), missed/delayed DID_RENEW
        # notifications, and the historical case where same-tier renewals
        # never reset. Atomic via WHERE-guarded UPDATE — racing requests
        # don't double-reset. No-op when not yet due.
        await lazy_reset_if_due(db, user.id)

        # Simulation: force allocation exhausted (admin testing toggle).
        # Kept on the 429 path because it's a synthetic dev affordance — the
        # production block path is the budget gate.
        if user.simulated_exhausted:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "allocation_exhausted",
                    "message": (
                        f"Monthly allocation exhausted "
                        f"(${effective_limit:.2f}/${effective_limit:.2f}). "
                        f"Simulated by admin toggle."
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

        # Read user's allocation state. Do NOT raise on over-cap — the
        # budget gate handles that case with the new envelope.
        cursor = await db.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?",
            (user.id,),
        )
        row = await cursor.fetchone()
        monthly_used = float(row["monthly_used_usd"] or 0) if row else 0.0

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
        app_id: str | None = None,
    ) -> None:
        # Build metadata from usage + cost dicts + raw request/response
        metadata: dict = {}
        if response and response.usage:
            metadata["usage"] = response.usage
        if response and response.cost:
            metadata["cost"] = response.cost
        if response and response.raw_request_json:
            metadata["raw_request"] = response.raw_request_json
        if response and response.raw_response_json:
            metadata["raw_response"] = response.raw_response_json
        # Documents passthrough (#359): count + raw byte total, set by
        # app/services/documents.py regardless of which path each file took.
        if request.get_meta("document_count"):
            metadata["documents"] = {
                "count": request.get_meta("document_count"),
                "raw_bytes": request.get_meta("document_bytes"),
            }

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
                image_count, session_duration_sec, cached_tokens, meeting_id, metadata, app_id, scenario, scenario_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                request.get_meta("call_type"),
                request.get_meta("prompt_mode"),
                request.get_meta("image_count") or (len(request.images) if request.images else 0),
                request.get_meta("session_duration_sec"),
                cached_tokens,
                request.get_meta("meeting_id"),
                metadata_json,
                app_id,
                _normalize_scenario(request.get_meta("scenario")),
                _normalize_scenario_kind(request.get_meta("scenario_kind")),
            ),
        )
        await db.commit()

    async def record_and_log(
        self,
        db: aiosqlite.Connection,
        *,
        user: UserRecord,
        tier: TierDefinition,
        app_id: str | None,
        request: ChatRequest,
        response: ChatResponse | None,
        elapsed_ms: int,
        pricing,
        status: str = "success",
    ) -> None:
        """Cost + meter a single LLM call in one shot: compute cost from the
        pricing table, deduct it from the user's allocation, and write the
        usage_log row.

        Mirrors the inline cost/record/log sequence the chat and report handlers
        run for their primary call. Used for sub-calls that would otherwise go
        unmetered — e.g. the captions_cleanup pass that runs before analysis and
        report generation (see app.services.transcript_cleanup), which spends
        real tokens on a second model but never surfaced in the Query Log, cost
        totals, or budget before this.
        """
        request_cost = 0.0
        if response and getattr(pricing, "is_loaded", False):
            cost = pricing.calculate_cost(
                provider=request.provider,
                model=request.model,
                usage=response.usage,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            response.cost = cost
            request_cost = cost.get("total_cost", 0.0)
        await self.record_cost(db, user.id, request_cost, tier, user=user)
        await self.log_usage(
            db, user.id, request, response, elapsed_ms, status=status, app_id=app_id,
        )
