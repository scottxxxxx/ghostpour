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
        # Memory contract v1 (CQ working session 2026-07-15/16). The
        # allowlist IS the extension point — CQ names a key, we add a line:
        # memory_signals: client passthrough; CQ renders explicit "(no
        # stored memory about: X)" lines inside the block so the model
        # stops inventing around gaps. SS flips it per surface.
        if body.get_meta("memory_signals") is not None:
            cq_metadata["memory_signals"] = body.get_meta("memory_signals")
        # token_budget: GP-set per surface — project chats get the scoped
        # block budget (commitments/blockers with the overdue guarantee
        # need more room than the 700-token default); other surfaces keep
        # CQ's default.
        if body.get_meta("prompt_mode") == "ProjectChat":
            cq_metadata["token_budget"] = 1200

        if feature_state == "enabled":
            # Correction lane (Contract item 9, dark until CQ's handler is
            # live): the user is correcting stored memory in place. Detect
            # now; FIRE after recall/dossier injection so the freshly
            # injected block rides as the candidate set — CQ's within-day
            # byte stability makes this turn's block the same one the user
            # was looking at. Capture carries the user's words + scope,
            # NEVER the model's response. The steering line keeps the
            # acknowledgment honest: capture confirms QUEUEING, not
            # application, so the words are "updating", never "updated".
            _correction_qp = None
            if get_settings().cq_corrections_enabled:
                from app.services.document_generation import _question_portion
                _qp = _question_portion(body.user_content)
                if cq.is_correction_ask(_qp):
                    _correction_qp = _qp

            def _fire_correction(b: ChatRequest) -> ChatRequest:
                if _correction_qp is None:
                    return b
                asyncio.create_task(cq.capture(
                    user_id=user.id,
                    interaction_type="correction",
                    content=_correction_qp,
                    origin_id=b.get_meta("origin_id"),
                    origin_type=b.get_meta("origin_type"),
                    project=b.get_meta("project"),
                    project_id=b.get_meta("project_id"),
                    prompt_mode=b.get_meta("prompt_mode"),
                    display_name=user.display_name,
                    email=user.email,
                    subscription_tier=user.effective_tier,
                    context_block=(b.metadata or {}).get("cq_recall_block"),
                ))
                steer = (
                    "MEMORY CORRECTION: the user is correcting stored "
                    "memory. Their correction has been queued to the "
                    "memory system; it applies shortly, not instantly, and "
                    "the context above may still show the old version this "
                    "turn. Acknowledge naturally that the record is being "
                    "updated (say it is updating, never that it is already "
                    "updated), treat the user's stated version as correct, "
                    "and answer any remaining question normally.")
                return b.model_copy(update={
                    "system_prompt": b.system_prompt + "\n\n" + steer})

            # Rundown routing (Context Flow Contract v1, item 3): an
            # inventory-style ask with a project scope gets the complete
            # meeting-grouped dossier instead of the ranked recall block —
            # recall is the wrong tool for "give me everything" by design
            # (live 2026-07-15: the rundown query matched 1 entity while
            # CQ held 98 scoped patches). Deterministic detection on the
            # question portion; ANY miss or failure falls open to recall.
            if body.get_meta("project_id"):
                from app.services.document_generation import _question_portion
                if cq.is_rundown_ask(_question_portion(body.user_content)):
                    dossier = await cq.quilt_dossier(
                        user.id, body.get_meta("project_id"))
                    if dossier and (dossier.get("meetings")
                                    or dossier.get("facts")
                                    or dossier.get("action_items")):
                        block = cq.format_dossier(dossier)
                        if "{{context_quilt}}" in body.system_prompt:
                            new_system = body.system_prompt.replace(
                                "{{context_quilt}}", block)
                        else:
                            new_system = block + "\n\n" + body.system_prompt
                        new_meta = dict(body.metadata or {})
                        new_meta["cq_recall_block"] = block
                        body = body.model_copy(update={
                            "system_prompt": new_system,
                            "metadata": new_meta,
                        })
                        result["cq_result"] = {
                            "context": block, "matched_entities": [],
                            "patch_count": sum(
                                len(m.get("patches") or [])
                                for m in dossier.get("meetings") or []),
                            "dossier": True,
                        }
                        return _fire_correction(body), result
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
                    new_system = body.system_prompt.replace("{{context_quilt}}", cq_context)
                else:
                    new_system = f"[CONTEXT FROM PREVIOUS MEETINGS]\n{cq_context}\n\n{body.system_prompt}"
                # Stash the exact recall text on metadata so cache-aware
                # adapters (Anthropic) can split the system prompt at the
                # recall boundary into separate cache_control blocks. Once
                # CQ #89 made recall byte-stable across calls within a 5-min
                # window, isolating the recall block lets the base prefix
                # cache independently when recall content differs across
                # turns. Adapters that don't consume this fall back to the
                # single-block string layout in `system_prompt` and behave
                # exactly as before.
                new_meta = dict(body.metadata or {})
                new_meta["cq_recall_block"] = cq_context
                body = body.model_copy(update={
                    "system_prompt": new_system,
                    "metadata": new_meta,
                })

            # Inject communication style for chat modes only
            if cq_result.get("communication_style") and body.get_meta("prompt_mode") in (
                "ProjectChat", "PostMeetingChat"
            ):
                body = body.model_copy(update={
                    "system_prompt": body.system_prompt + f"\n\n{cq_result['communication_style']}"
                })

            body = _fire_correction(body)

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
            language=body.get_meta("language") or body.locale,
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
