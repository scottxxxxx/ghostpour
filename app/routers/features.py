"""Feature-specific surface endpoints.

Currently hosts the Project Chat preflight check. Other per-feature
endpoints (preflight, status, etc.) can land here as the policy machinery
expands beyond Project Chat.
"""

from __future__ import annotations

import logging
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user import UserRecord
from app.services.project_chat_policy import (
    SelectedModel,
    render_cta_text,
    resolve_project_chat_verdict,
)
from app.services.project_chat_quota import read_quota_state

logger = logging.getLogger("ghostpour.features")

router = APIRouter()


class ProjectChatCheckRequest(BaseModel):
    selected_model: Literal["ssai", "external"]


def _get_project_chat_config(request: Request, locale: str | None) -> dict:
    """Resolve the project_chat feature_definitions block for a locale.

    Falls back to default tiers config, then features.yml. Returns a dict
    with at least gp_chat_flag, free_quota_per_month, cta_strings.
    """
    configs = request.app.state.remote_configs
    localized_name = f"tiers.{locale}" if locale else None

    for slug in (localized_name, "tiers"):
        if slug and slug in configs:
            pc = configs[slug].get("feature_definitions", {}).get("project_chat")
            if pc and pc.get("gp_chat_flag"):
                return pc

    # Fall back to features.yml (English source of truth)
    feature_config = request.app.state.feature_config
    pc_def = feature_config.features.get("project_chat")
    if pc_def:
        return {
            "gp_chat_flag": pc_def.gp_chat_flag or "plus",
            "free_quota_per_month": pc_def.free_quota_per_month,
            "cta_strings": pc_def.cta_strings,
        }

    # Final default — most restrictive policy if no config is loaded.
    return {
        "gp_chat_flag": "plus",
        "free_quota_per_month": 1,
        "cta_strings": {},
    }


@router.post("/features/project-chat/check")
async def project_chat_check(
    body: ProjectChatCheckRequest,
    request: Request,
    user: UserRecord | None = Depends(get_current_user_optional),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Read-only preflight for a Project Chat send.

    Returns a verdict telling iOS whether to:
    - send to GP (`/v1/chat` with prompt_mode=ProjectChat)
    - send to GP and expect a CTA on the response
    - route the query to the user's selected model client-side
    - render the login CTA inline

    Does not modify quota state. Decrements happen on actual GP
    processing in /v1/chat.
    """
    from app.routers.config import _parse_accept_language

    locale = _parse_accept_language(request.headers.get("Accept-Language"))
    pc_config = _get_project_chat_config(request, locale)

    gp_chat_flag = pc_config.get("gp_chat_flag", "plus")
    free_quota_per_month = pc_config.get("free_quota_per_month", 1)
    cta_strings = pc_config.get("cta_strings", {})

    is_logged_in = user is not None
    tier = user.effective_tier if user else None

    # Compute quota state (only meaningful when user is signed in and Free)
    quota = None
    if user and user.effective_tier == "free":
        quota = read_quota_state(user, free_quota_per_month)

    has_quota = quota.has_quota if quota else True

    result = resolve_project_chat_verdict(
        is_logged_in=is_logged_in,
        tier=tier,
        gp_chat_flag=gp_chat_flag,
        selected_model=body.selected_model,
        has_quota=has_quota,
        free_quota_per_month=free_quota_per_month,
    )

    response: dict = {
        "verdict": result.verdict,
        "policy_mode": gp_chat_flag,
    }

    # Surface quota fields when applicable (Free user, signed in)
    if quota is not None:
        response["quota_remaining"] = (
            quota.remaining if quota.remaining is not None else None
        )
        response["quota_total"] = quota.total
        response["quota_resets_at"] = quota.resets_at

    # Render CTA when the verdict carries one
    if result.cta_kind:
        cta_text = render_cta_text(
            result.cta_kind,
            cta_strings,
            remaining=quota.remaining if quota and quota.remaining is not None else 0,
            total=quota.total if quota else free_quota_per_month,
        )
        response["cta"] = {
            "kind": result.cta_kind,
            "text": cta_text,
        }

    headers = {"X-Locale-Resolved": locale or "en"} if locale else {}
    return JSONResponseWithHeaders(response, headers=headers)


# Helper — small wrapper to keep the response handler clean. FastAPI's
# default return-dict serialization doesn't accept custom headers, so
# we use JSONResponse explicitly.
from fastapi.responses import JSONResponse


def JSONResponseWithHeaders(content: dict, *, headers: dict) -> JSONResponse:
    return JSONResponse(content=content, headers=headers)
