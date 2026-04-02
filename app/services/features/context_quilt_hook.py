"""Context Quilt feature hook.

Implements the FeatureHook protocol for CQ integration:
  before_llm: recall context from CQ, inject into system prompt
  after_llm: capture query+response to CQ (async, non-blocking)
  response_headers: X-CQ-Matched, X-CQ-Entities, X-CQ-Gated
"""

import asyncio
import logging
from typing import Any

from app.models.chat import ChatRequest, ChatResponse
from app.models.feature import FeatureDefinition
from app.models.tier import TierDefinition
from app.models.user import UserRecord
from app.services import context_quilt as cq

logger = logging.getLogger(__name__)


class ContextQuiltHook:
    def __init__(self, feature_def: FeatureDefinition | None = None):
        self._skip_modes = set(feature_def.capture_skip_modes) if feature_def else set()

    async def before_llm(
        self,
        user: UserRecord,
        body: ChatRequest,
        tier: TierDefinition,
        feature_state: str,
        skip_teasers: set[str],
    ) -> tuple[ChatRequest, dict[str, Any]]:
        result: dict[str, Any] = {
            "cq_result": {"context": "", "matched_entities": [], "patch_count": 0},
            "gated": False,
        }

        if not body.context_quilt:
            return body, result

        # Build CQ metadata from request
        cq_metadata = {}
        if body.get_meta("project"):
            cq_metadata["project"] = body.get_meta("project")
        if body.get_meta("project_id"):
            cq_metadata["project_id"] = body.get_meta("project_id")

        if feature_state == "enabled":
            # Full CQ: recall + inject
            cq_result = await cq.recall(
                user_id=user.id,
                text=body.user_content,
                metadata=cq_metadata or None,
            )
            result["cq_result"] = cq_result

            if cq_result.get("context"):
                cq_context = cq_result["context"]
                if "{{context_quilt}}" in body.system_prompt:
                    body = body.model_copy(update={
                        "system_prompt": body.system_prompt.replace("{{context_quilt}}", cq_context)
                    })
                else:
                    body = body.model_copy(update={
                        "system_prompt": f"[CONTEXT FROM PREVIOUS MEETINGS]\n{cq_context}\n\n{body.system_prompt}"
                    })

            # Inject communication style for chat modes only
            if cq_result.get("communication_style") and body.get_meta("prompt_mode") in (
                "ProjectChat", "PostMeetingChat"
            ):
                body = body.model_copy(update={
                    "system_prompt": body.system_prompt + f"\n\n{cq_result['communication_style']}"
                })

        elif feature_state == "teaser" and "context_quilt" not in skip_teasers:
            # Teaser: recall for metadata only, don't inject
            cq_result = await cq.recall(
                user_id=user.id,
                text=body.user_content,
                metadata=cq_metadata or None,
            )
            result["cq_result"] = cq_result
            if cq_result.get("matched_entities"):
                result["gated"] = True

        return body, result

    async def after_llm(
        self,
        user: UserRecord,
        body: ChatRequest,
        response: ChatResponse,
        hook_result: dict[str, Any],
        feature_state: str,
    ) -> None:
        if feature_state != "enabled" or not body.context_quilt:
            return

        prompt_mode = body.get_meta("prompt_mode")
        session_duration = body.get_meta("session_duration_sec")

        # Skip capture for read-only modes and active sessions
        if prompt_mode in self._skip_modes or session_duration is not None:
            return

        asyncio.create_task(cq.capture(
            user_id=user.id,
            interaction_type=body.get_meta("call_type") or "query",
            content=body.user_content,
            response=response.text,
            meeting_id=body.get_meta("meeting_id"),
            project=body.get_meta("project"),
            project_id=body.get_meta("project_id"),
            call_type=body.get_meta("call_type"),
            prompt_mode=prompt_mode,
            display_name=user.display_name,
            email=user.email,
        ))

    def response_headers(
        self,
        hook_result: dict[str, Any],
        feature_state: str,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        cq_result = hook_result.get("cq_result", {})
        matched = cq_result.get("matched_entities", [])
        gated = hook_result.get("gated", False)

        if feature_state == "enabled" and matched:
            headers["X-CQ-Matched"] = str(len(matched))
            headers["X-CQ-Entities"] = ",".join(matched[:10])
        elif gated:
            headers["X-CQ-Matched"] = str(len(matched))
            headers["X-CQ-Gated"] = "true"
            if matched:
                headers["X-CQ-Entities"] = ",".join(matched[:10])

        return headers
