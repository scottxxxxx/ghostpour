"""POST /v1/translations — the meeting-translation engine.

Contract: docs/wire-contracts/meeting-translations.md. Ungated by tier
(Scott 2026-08-24: no per-feature gates; the monthly dollar cap is the
gate), metered into the same budget path as chat, managed-only.
"""
from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import get_db
from app.dependencies import get_current_user
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
    try:
        out, cached = await tr.translate_group(
            request.app.state, db, user, segments, source, target, body.artifact,
            app_id=getattr(request.state, "app_id", None),
            request_id=getattr(request.state, "request_id", None))
    except tr.TranslationBlocked as e:
        if str(e) == "unknown_tier":
            raise HTTPException(status_code=403, detail={"code": "unknown_tier"})
        raise HTTPException(status_code=429, detail={
            "code": "allocation_exhausted",
            "message": "Monthly AI allocation reached. Translations resume when it resets, or with a higher plan.",
        })
    except tr.TranslationFailed as e:
        raise HTTPException(status_code=502, detail={
            "code": str(e), "message": "Translation failed; retry this group"})
    return {
        "segments": out,
        "source_language": source,
        "target_language": target,
        "engine_version": tr.ENGINE_VERSION,
        "cached": cached,
    }
