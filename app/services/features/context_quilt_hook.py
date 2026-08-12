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
        app_id: str | None = None,
    ) -> tuple[ChatRequest, dict[str, Any]]:
        result: dict[str, Any] = {
            "cq_result": {"context": "", "matched_entities": [], "patch_count": 0},
            "gated": False,
        }

        if feature_state == "disabled":
            # People-scoped recall lane. The dispatch in chat.py only
            # routes a disabled-state call here when the people
            # entitlement is enabled, so reaching this branch IS the free
            # lane; the dashboard People toggle closes it at the dispatch.
            return await self._people_scoped_recall(user, body, result, app_id)

        if not body.context_quilt:
            return body, result

        cq_metadata = _build_recall_metadata(body)

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
            _completion_qp = None
            _s = get_settings()
            if _s.cq_corrections_enabled or _s.cq_completions_enabled:
                from app.services.document_generation import _question_portion
                _qp = _question_portion(body.user_content)
                if _s.cq_corrections_enabled and cq.is_correction_ask(_qp):
                    _correction_qp = _qp
                if _s.cq_completions_enabled and cq.is_completion_ask(_qp):
                    _completion_qp = _qp

            def _fire_correction(b: ChatRequest) -> ChatRequest:
                if _correction_qp is None:
                    return b
                asyncio.create_task(cq.capture(
                    app_id=app_id,
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

            def _fire_completion(b: ChatRequest) -> ChatRequest:
                # Item 10: same pipe as tap-to-complete — the capture
                # carries the user's words + the on-screen block; CQ's
                # commitment-resolution matcher closes the patch and the
                # completed array flows through delta sync.
                if _completion_qp is None:
                    return b
                asyncio.create_task(cq.capture(
                    app_id=app_id,
                    user_id=user.id,
                    interaction_type="completion",
                    content=_completion_qp,
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
                    "MEMORY COMPLETION: the user says a tracked commitment "
                    "or blocker is done. The completion has been queued to "
                    "the memory system; it applies shortly, not instantly, "
                    "and the context above may still show it open this "
                    "turn. Acknowledge naturally that the item is being "
                    "marked complete (never say it is already closed), and "
                    "answer any remaining question normally.")
                return b.model_copy(update={
                    "system_prompt": b.system_prompt + "\n\n" + steer})

            def _fire_memory_edits(b: ChatRequest) -> ChatRequest:
                return _fire_completion(_fire_correction(b))

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
                        user.id, body.get_meta("project_id"), app_id=app_id)
                    if dossier and (dossier.get("meetings")
                                    or dossier.get("facts")
                                    or dossier.get("action_items")):
                        block = cq.format_dossier(dossier)
                        if "{{context_quilt}}" in body.system_prompt:
                            new_system = body.system_prompt.replace(
                                "{{context_quilt}}", block)
                        else:
                            # APPEND, not prepend. See the note on the other
                            # injection path below: the envelope declares
                            # context_quilt last in the system block, and
                            # prepending put a per-turn block ahead of
                            # everything we want cached.
                            new_system = body.system_prompt + "\n\n" + block
                        new_meta = dict(body.metadata or {})
                        new_meta["cq_recall_block"] = block
                        body = body.model_copy(update={
                            "system_prompt": new_system,
                            "metadata": new_meta,
                            # A rundown answer summarizes the whole dossier
                            # — the first live run hit the standard 4096
                            # output ceiling mid-write (2026-07-16
                            # 14:38:44Z, out_tokens == max_tokens exactly).
                            "max_tokens": max(body.max_tokens or 0, 8000),
                        })
                        result["cq_result"] = {
                            "context": block, "matched_entities": [],
                            "patch_count": sum(
                                len(m.get("patches") or [])
                                for m in dossier.get("meetings") or []),
                            "dossier": True,
                        }
                        return _fire_memory_edits(body), result
            # Full CQ: recall + inject
            cq_result = await cq.recall(
                app_id=app_id,
                user_id=user.id,
                text=body.user_content,
                metadata=cq_metadata or None,
                subscription_tier=user.effective_tier,
            )
            result["cq_result"] = cq_result

            if cq_result.get("context"):
                body = _inject_recall_block(body, cq_result["context"])

            # Inject communication style for chat modes only
            if cq_result.get("communication_style") and body.get_meta("prompt_mode") in (
                "ProjectChat", "PostMeetingChat"
            ):
                body = body.model_copy(update={
                    "system_prompt": body.system_prompt + f"\n\n{cq_result['communication_style']}"
                })

            body = _fire_memory_edits(body)

        elif feature_state == "teaser" and "context_quilt" not in skip_teasers:
            # Teaser: recall for metadata only, don't inject
            cq_result = await cq.recall(
                app_id=app_id,
                user_id=user.id,
                text=body.user_content,
                metadata=cq_metadata or None,
                subscription_tier=user.effective_tier,
            )
            result["cq_result"] = cq_result
            if cq_result.get("matched_entities"):
                result["gated"] = True

        return body, result

    async def _people_scoped_recall(
        self,
        user: UserRecord,
        body: ChatRequest,
        result: dict[str, Any],
        app_id: str | None,
    ) -> tuple[ChatRequest, dict[str, Any]]:
        """Free lane: recall scoped to what the People tab shows.

        Decision 2026-08-11: People launches at full value on every tier,
        and the assistant may know exactly what the user's own screens
        show them. `recall_scope: "people"` selects CQ's tab-equivalent
        scoped render; the key ABSENT means full scope, which is why the
        enabled lane never sends it.

        Deliberately NOT gated on body.context_quilt (Scott, option one,
        2026-08-11): the client sends that flag by SERVED entitlement
        state, so free builds never send it, and BYOK plus Apple-FM
        traffic bypasses GP entirely. Anything reaching this hook is
        CloudZap traffic, and the server-side entitlement alone decides.

        Everything else the enabled lane does stays enabled-only on
        purpose: rundown dossiers, the correction and completion lanes,
        the communication-style line, and the after_llm capture (which
        remains gated to feature_state == "enabled", so free chat turns
        write nothing; the metered transcript path is the only free write
        path).

        TODO(people-lane-copy): the MEMORY CAPABILITY steering line in
        chat.py only serves users whose context_quilt entitlement is
        enabled. What a free user's assistant should SAY about the memory
        it does and does not have needs a copy pass of its own; do not
        invent copy here.
        """
        cq_metadata = _build_recall_metadata(body)
        cq_metadata["recall_scope"] = "people"
        cq_result = await cq.recall(
            app_id=app_id,
            user_id=user.id,
            text=body.user_content,
            metadata=cq_metadata,
            subscription_tier=user.effective_tier,
        )
        result["cq_result"] = cq_result
        if cq_result.get("context"):
            body = _inject_recall_block(body, cq_result["context"])
        return body, result

    async def after_llm(
        self,
        user: UserRecord,
        body: ChatRequest,
        response: ChatResponse,
        hook_result: dict[str, Any],
        feature_state: str,
        app_id: str | None = None,
    ) -> None:
        if feature_state != "enabled" or not body.context_quilt:
            return

        prompt_mode = body.get_meta("prompt_mode")
        session_duration = body.get_meta("session_duration_sec")

        # Skip capture for read-only modes and active sessions
        if prompt_mode in self._skip_modes or session_duration is not None:
            return

        asyncio.create_task(cq.capture(
            app_id=app_id,
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


def _build_recall_metadata(body: ChatRequest) -> dict[str, Any]:
    """Compose the outbound recall metadata from the request.

    Key by key on purpose: this composition IS the allowlist, the same
    extension point capture uses. CQ names a key, we add a line, and
    nothing a client puts in its own metadata reaches CQ without one.
    That is also what keeps entitlement-derived keys unspoofable:
    recall_scope and subscription_tier are set server-side after this
    runs, never copied from the request, so a client can neither widen a
    free user's scope nor narrow a paid one's.
    """
    cq_metadata: dict[str, Any] = {}
    if body.get_meta("project"):
        cq_metadata["project"] = body.get_meta("project")
    if body.get_meta("project_id"):
        cq_metadata["project_id"] = body.get_meta("project_id")
    cq_metadata["locale"] = body.get_meta("locale") or "en"
    if body.get_meta("owner_speaker_label"):
        cq_metadata["owner_speaker_label"] = body.get_meta("owner_speaker_label")
    # Memory contract v1 (CQ working session 2026-07-15/16).
    # memory_signals: client passthrough; CQ renders explicit "(no stored
    # memory about: X)" lines inside the block so the model stops
    # inventing around gaps. SS flips it per surface.
    if body.get_meta("memory_signals") is not None:
        cq_metadata["memory_signals"] = body.get_meta("memory_signals")
    # token_budget: GP-set per surface. Project chats get the scoped block
    # budget (commitments and blockers with the overdue guarantee need
    # more room than the 700-token default); other surfaces keep CQ's
    # default.
    if body.get_meta("prompt_mode") == "ProjectChat":
        cq_metadata["token_budget"] = 1200
    return cq_metadata


def _inject_recall_block(body: ChatRequest, cq_context: str) -> ChatRequest:
    """Sanitize and place a recall block, and stash it for the cache split.

    Sanitizer: strip "(you)" suffixes so the LLM does not echo them
    ("Scott (you) decided..."). Render-time fix for historical patches;
    CQ #43 and #93 tightened upstream extraction so new patches should
    not carry the suffix, and CZ_CQ_DISABLE_YOU_SUFFIX_SANITIZER=true on
    a canary lets us verify unsanitized recall is grammatical before the
    regex retires.

    Placement: fill the {{context_quilt}} placeholder when the client's
    template carries it; otherwise APPEND, not prepend (2026-08-03). The
    chat surfaces do not emit the placeholder, so the fallback decides
    their recall position, and prepending put the per-turn block at index
    0, ahead of everything the envelope declares cacheable. CQ settled
    the property this rests on: contract item 6 promises determinism for
    a repeated IDENTICAL request (the scorer buckets time to the UTC day
    so a freshness penalty cannot step by the second), not invariance
    across different questions, so recall is per-turn volatile and the
    envelope places it LAST in the system block. Appending puts it where
    the spec says.

    Stash: the exact recall text rides metadata.cq_recall_block so
    cache-aware adapters (Anthropic) can split the system prompt at the
    recall boundary into separate cache_control blocks. Once CQ #89 made
    recall byte-stable across calls within a 5-minute window, isolating
    the block lets the base prefix cache independently when recall
    content differs across turns. Adapters that do not consume it fall
    back to the single-block string in `system_prompt` and behave exactly
    as before.
    """
    if not get_settings().cq_disable_you_suffix_sanitizer:
        cq_context = _sanitize_you_suffix(cq_context)
    base = body.system_prompt or ""
    if "{{context_quilt}}" in base:
        new_system = base.replace("{{context_quilt}}", cq_context)
    else:
        new_system = f"{base}\n\n[CONTEXT FROM PREVIOUS MEETINGS]\n{cq_context}"
    new_meta = dict(body.metadata or {})
    new_meta["cq_recall_block"] = cq_context
    return body.model_copy(update={
        "system_prompt": new_system,
        "metadata": new_meta,
    })


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
