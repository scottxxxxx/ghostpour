"""Context Quilt feature hook.

Implements the FeatureHook protocol for CQ integration:
  before_llm: recall context from CQ, inject into system prompt
  after_llm: capture query+response to CQ (async, non-blocking)
  response_headers: X-CQ-Matched, X-CQ-Entities, X-CQ-Gated
"""

import asyncio
import logging
import re
from typing import Any

from app.config import get_settings
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
        cq_metadata["locale"] = body.get_meta("locale") or "en"
        if body.get_meta("owner_speaker_label"):
            cq_metadata["owner_speaker_label"] = body.get_meta("owner_speaker_label")

        if feature_state == "enabled":
            # Full CQ: recall + inject
            cq_result = await cq.recall(
                user_id=user.id,
                text=body.user_content,
                metadata=cq_metadata or None,
                subscription_tier=user.effective_tier,
            )
            result["cq_result"] = cq_result

            if cq_result.get("context"):
                cq_context = cq_result["context"]
                # Sanitize "(you)" suffixes from CQ context to prevent the LLM
                # from echoing them in output (e.g., "Scott (you) decided...").
                # Kill-switch — CQ #43 + #93 tightened upstream extraction so
                # new patches shouldn't carry the "(you)" suffix; setting
                # CZ_CQ_DISABLE_YOU_SUFFIX_SANITIZER=true on a canary lets us
                # verify unsanitized recall is grammatical before retiring
                # the regex.
                if not get_settings().cq_disable_you_suffix_sanitizer:
                    cq_context = _sanitize_you_suffix(cq_context)
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
                subscription_tier=user.effective_tier,
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
            origin_id=body.get_meta("origin_id"),
            origin_type=body.get_meta("origin_type"),
            # Deprecated alias — still honored for clients that haven't
            # migrated; cq.capture() translates it to origin_id/origin_type.
            meeting_id=body.get_meta("meeting_id"),
            project=body.get_meta("project"),
            project_id=body.get_meta("project_id"),
            call_type=body.get_meta("call_type"),
            prompt_mode=prompt_mode,
            display_name=user.display_name,
            email=user.email,
            user_identified=body.get_meta("user_identified"),
            user_label=body.get_meta("user_label"),
            identification_source=body.get_meta("identification_source"),
            subscription_tier=user.effective_tier,
        ))

    def response_headers(
        self,
        hook_result: dict[str, Any],
        feature_state: str,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        cq_result = hook_result.get("cq_result", {})
        matched = cq_result.get("matched_entities", [])
        patch_ids = cq_result.get("matched_patch_ids", [])
        gated = hook_result.get("gated", False)

        if feature_state == "enabled" and matched:
            headers["X-CQ-Matched"] = str(len(matched))
            headers["X-CQ-Entities"] = ",".join(matched[:10])
            if patch_ids:
                headers["X-CQ-Patch-IDs"] = ",".join(patch_ids[:20])
        elif gated:
            headers["X-CQ-Matched"] = str(len(matched))
            headers["X-CQ-Gated"] = "true"
            if matched:
                headers["X-CQ-Entities"] = ",".join(matched[:10])
            if patch_ids:
                headers["X-CQ-Patch-IDs"] = ",".join(patch_ids[:20])

        return headers


def _sanitize_you_suffix(text: str) -> str:
    """Strip '(you)' suffixes from CQ context to prevent LLM echo.

    Rewrites patterns like 'Scott (you) wants...' → 'You want...'
    and 'Name (you)' → 'You' in any position. Also handles bracketed
    forms like '[Scott (you)]' → '[You]'.

    This is a render-time fix for historical patches stored with the
    '(you)' suffix. New patches should use second-person 'You' natively.
    """
    # Replace "Name (you)" patterns with "You"
    # Handles: "Scott (you)", "[Scott (you)]", "Speaker 1 (you)"
    text = re.sub(r'\b\w[\w\s]*?\s*\(you\)', 'You', text, flags=re.IGNORECASE)
    # Clean up any remaining standalone "(you)" that might be left
    text = re.sub(r'\s*\(you\)', '', text, flags=re.IGNORECASE)
    return text
