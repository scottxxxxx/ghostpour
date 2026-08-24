"""POST /v1/translations — the meeting-translation engine.

Contract: docs/wire-contracts/meeting-translations.md. Ungated by tier
(Scott 2026-08-24: no per-feature gates; the monthly dollar cap is the
gate), metered into the same budget path as chat, managed-only.
"""
from __future__ import annotations

import logging
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest
from app.models.user import UserRecord
from app.services import translations as tr

logger = logging.getLogger("ghostpour.translations")
router = APIRouter()

MAX_SEGMENTS = 100
MAX_GROUP_CHARS = 60_000


class TranslationSegment(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=20_000)


class TranslationRequest(BaseModel):
    source_language: str
    target_language: str
    artifact: str
    segments: list[TranslationSegment] = Field(min_length=1, max_length=MAX_SEGMENTS)


@router.post("/translations")
async def translate(
    body: TranslationRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    source = tr.normalize_language(body.source_language)
    target = tr.normalize_language(body.target_language)
    if not source or not target:
        # 422, never a guess: stating the language is the doctrine.
        raise HTTPException(status_code=422, detail={
            "code": "invalid_language",
            "message": "source_language and target_language must be BCP-47 tags",
        })
    if body.artifact not in tr.ARTIFACTS:
        raise HTTPException(status_code=422, detail={
            "code": "invalid_artifact",
            "message": f"artifact must be one of {list(tr.ARTIFACTS)}",
        })
    if sum(len(s.text) for s in body.segments) > MAX_GROUP_CHARS:
        raise HTTPException(status_code=413, detail={
            "code": "group_too_large",
            "message": "Split this group: it exceeds the per-request character cap",
        })

    segments = [{"id": s.id, "text": s.text} for s in body.segments]
    key = tr.cache_key(segments, source, target)

    def _envelope(out_segments, cached):
        return {
            "segments": out_segments,
            "source_language": source,
            "target_language": target,
            "engine_version": tr.ENGINE_VERSION,
            "cached": cached,
        }

    cached = await tr.cache_get(db, key)
    if cached is not None:
        return _envelope(cached, True)

    # Budget pre-gate: same gate as chat/reports. The cap is THE gate —
    # there is no tier gate on this feature by ruling.
    tier = request.app.state.tier_config.tiers.get(user.effective_tier)
    if tier is None:
        raise HTTPException(status_code=403, detail={"code": "unknown_tier"})
    effective_limit = tier.monthly_cost_limit_usd
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        effective_limit = tier.trial_cost_limit_usd
    if effective_limit != -1:
        row = await (await db.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?", (user.id,)
        )).fetchone()
        monthly_used = float(row["monthly_used_usd"] or 0) if row else 0.0
        if monthly_used >= effective_limit:
            raise HTTPException(status_code=429, detail={
                "code": "allocation_exhausted",
                "message": "Monthly AI allocation reached. Translations resume when it resets, or with a higher plan.",
            })

    import json as _json
    expected_ids = [s["id"] for s in segments]
    chat_request = ChatRequest(
        provider=tr.TRANSLATION_PROVIDER,
        model=tr.TRANSLATION_MODEL,
        system_prompt=tr.system_prompt(body.artifact),
        user_content=(
            f"Target language: {target}. Source language: {source}.\n"
            + _json.dumps(segments, ensure_ascii=False)
        ),
        max_tokens=8192,
        temperature=0.2,
        metadata={
            "call_type": "translation",
            "request_id": getattr(request.state, "request_id", None),
            "translation_artifact": body.artifact,
            "translation_segments": len(segments),
            "translation_source": source,
            "translation_target": target,
        },
    )

    provider_router = request.app.state.provider_router
    start = time.monotonic()
    out = None
    for attempt in (1, 2):
        try:
            response = await provider_router.route(chat_request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("translation LLM call failed: %s", e)
            raise HTTPException(status_code=502, detail={
                "code": "provider_error", "message": "Translation failed; retry this group",
            })
        out = tr.parse_model_output(response.text or "", expected_ids)
        if out is not None:
            break
        logger.warning("translation id round-trip failed attempt=%d", attempt)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Metering: cost + usage row (call_type=translation feeds the
    # dashboard Transl column and the budget the same way chat does).
    pricing = request.app.state.pricing
    usage_tracker = request.app.state.usage_tracker
    request_cost = 0.0
    if pricing.is_loaded and response is not None:
        cost = pricing.calculate_cost(
            provider=tr.TRANSLATION_PROVIDER, model=tr.TRANSLATION_MODEL,
            usage=response.usage,
            input_tokens=response.input_tokens, output_tokens=response.output_tokens,
        )
        response.cost = cost
        request_cost = cost.get("total_cost", 0.0)
    await usage_tracker.record_cost(db, user.id, request_cost, tier, user=user)
    await usage_tracker.log_usage(
        db, user.id, chat_request, response, elapsed_ms,
        status="success" if out is not None else "error",
        error_msg=None if out is not None else "id_round_trip_failed",
        app_id=getattr(request.state, "app_id", "unknown"),
    )

    if out is None:
        raise HTTPException(status_code=502, detail={
            "code": "translation_shape_error",
            "message": "Translation did not round-trip; retry this group",
        })

    await tr.cache_put(db, key, body.artifact, source, target, out)
    return _envelope(out, False)
