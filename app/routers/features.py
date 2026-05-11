"""Feature-specific surface endpoints.

Currently hosts the Project Chat preflight check. Other per-feature
endpoints (preflight, status, etc.) can land here as the policy machinery
expands beyond Project Chat.
"""

from __future__ import annotations

import logging
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user import UserRecord
from app.services.project_chat_policy import (
    SelectedModel,
    resolve_project_chat_verdict,
)

logger = logging.getLogger("ghostpour.features")

router = APIRouter()


class ProjectChatCheckRequest(BaseModel):
    selected_model: Literal["ssai", "external"]


def _get_project_chat_config(request: Request, locale: str | None) -> dict:
    """Resolve the project_chat feature_definitions block for a locale.

    Falls back to default tiers config, then features.yml. Returns a dict
    with at least gp_chat_flag.
    """
    configs = request.app.state.remote_configs
    localized_name = f"tiers.{locale}" if locale else None

    for slug in (localized_name, "tiers"):
        if slug and slug in configs:
            pc = configs[slug].get("feature_definitions", {}).get("project_chat")
            if pc and pc.get("gp_chat_flag"):
                return pc

    feature_config = request.app.state.feature_config
    pc_def = feature_config.features.get("project_chat")
    if pc_def:
        return {"gp_chat_flag": pc_def.gp_chat_flag or "plus"}

    return {"gp_chat_flag": "plus"}


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
    - route the query to the user's selected model client-side
    - render the login CTA inline

    Free-tier metering is handled by the budget gate on /v1/chat, not here.
    """
    from app.routers.config import _parse_accept_language

    locale = _parse_accept_language(request.headers.get("Accept-Language"))
    pc_config = _get_project_chat_config(request, locale)

    gp_chat_flag = pc_config.get("gp_chat_flag", "plus")

    is_logged_in = user is not None
    tier = user.effective_tier if user else None

    result = resolve_project_chat_verdict(
        is_logged_in=is_logged_in,
        tier=tier,
        gp_chat_flag=gp_chat_flag,
        selected_model=body.selected_model,
    )

    response: dict = {
        "verdict": result.verdict,
        "policy_mode": gp_chat_flag,
    }

    if result.cta_kind:
        cta_strings = pc_config.get("cta_strings", {}) or {}
        response["cta"] = {
            "kind": result.cta_kind,
            "text": cta_strings.get(result.cta_kind, ""),
        }

    headers = {"X-Locale-Resolved": locale or "en"} if locale else {}
    return JSONResponse(content=response, headers=headers)
