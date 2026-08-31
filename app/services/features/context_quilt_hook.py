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


# Placed immediately BEFORE any injected context block, never after.
#
# 2026-08-30: a 31 second recording tagged to an unrelated project produced
# a summary that, unable to find matching content, explained itself by
# LISTING the injected context: a real customer engagement, named
# individuals, an overdue commitment and a promotion decision, none of which
# were in the recording. Recall scoping is a separate and larger piece of
# work; it shrinks the blast radius but it does not make reciting safe, so
# this guard is independent of anything CQ changes.
#
# The rule that does the work is the LAST sentence: forbid naming what
# appears ONLY in the context. A blanket "never mention these names" would
# be wrong, because in a meeting that genuinely IS about that engagement the
# same names are legitimately grounded in the transcript.
#
# Placement is load-bearing. `_inject_recall_block` stashes the block on
# metadata.cq_recall_block and the Anthropic adapter slices system_prompt at
# that boundary. Text placed AFTER the block turns an empty suffix into a
# non-empty one, which flips recall from the uncached tail into a
# cache_control breakpoint covering prefix+recall — an entry that changes
# every turn and can never be reused. Before the block, this rides in the
# prefix that was already being cached.
RECALL_USE_GUARD = (
    "[HOW TO USE THE CONTEXT BELOW]\n"
    "This context is background reference for you. It is not part of the "
    "recording or document under discussion, and the user has not seen it.\n"
    "Draw on it when it genuinely informs your answer. Do not enumerate it, "
    "quote it, summarize it, or otherwise report its contents back to the "
    "user as an account of what you were given.\n"
    "If the material under discussion does not relate to this context, say "
    "plainly that it does not cover that ground and stop there. Do not name "
    "any project, person, client, organization, system, decision, or "
    "commitment that appears in this context but not in the material itself. "
    "Listing what the context contained is never an acceptable way to "
    "explain that it did not apply.\n"
    # The clause below is what actually does the work, and it was added only
    # after MEASURING that the prohibition above does not. Replaying the real
    # incident turn against the deployed prompt: the prohibition alone took
    # the recital from 17/48 runs to 5/48. Better, not fixed. A prohibition
    # tells the model what not to do and leaves it to invent a replacement,
    # and the replacement it invents is to explain itself, which means naming
    # the thing it was told not to name. Giving it the replacement outright
    # took it to 0/48.
    #
    # Deliberately surface-NEUTRAL. This guard rides on Project Chat and
    # dossier turns too, so it must not say "recording" or "summarize"; the
    # first version that measured 0 did, and would have been a wrong answer
    # in chat. The neutral wording was re-measured, not assumed.
    "If the material does not let you answer, your ENTIRE reply must be a "
    "single short sentence saying that the material does not cover it, and "
    "nothing else. Do not explain what you expected to find, do not contrast "
    "the material against this context, and do not name anything from this "
    "context. Naming context material while declining is the worst possible "
    "outcome and is never acceptable."
)


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
        recall_max_age_days: int | None = None,
        recall_timeout_ms: int | None = None,
    ) -> tuple[ChatRequest, dict[str, Any]]:
        result: dict[str, Any] = {
            "cq_result": {"context": "", "matched_entities": [], "patch_count": 0},
            "gated": False,
        }

        if feature_state in ("disabled", "teaser"):
            # People-scoped recall lane. The dispatch in chat.py only
            # routes a disabled-state call here when the people
            # entitlement is enabled, so reaching this branch IS the free
            # lane; the dashboard People toggle closes it at the dispatch.
            # Teaser without the client flag (every Free build, 2026-08-24
            # flip) is the same lane: the flag is what selects the hook's
            # own metadata-only teaser recall below.
            return await self._people_scoped_recall(
                user, body, result, app_id, recall_max_age_days,
                recall_timeout_ms)

        if not body.context_quilt:
            return body, result

        # A gate that declines SILENTLY has no instrument. Without this line,
        # "the gate fired", "recall returned nothing" and "recall was never
        # called" collapse into one indistinguishable observable, and "is the
        # gate working?" becomes neither verifiable nor falsifiable. CQ hit
        # exactly this in their own repo (their #350: two early exits
        # returning empty with no log line, which cost a prod query, an env
        # dump, a log grep and a read of the gate to establish what one line
        # now says). The reason is the computed comparison, not a boolean
        # spelling of it, so the log says WHY and against what.
        _material_chars = len(body.user_content or "")
        _floor = _material_floor_chars()
        if _material_below_floor(body):
            result["recall_skipped"] = "material_below_floor"
            logger.info(
                "cq_recall_skipped_thin_material",
                extra={"call_type": body.get_meta("call_type"),
                       "material_chars": _material_chars,
                       "floor_chars": _floor,
                       "reason": f"{_material_chars} < {_floor}"},
            )
            return body, result
        # Emitted on the INJECT path too, so the population that was gated
        # and the population that was not are the same query with a filter
        # rather than two reconstructions joined after the fact.
        result["material_chars"] = _material_chars

        cq_metadata = _build_recall_metadata(body)
        _apply_recall_window(cq_metadata, recall_max_age_days)

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
                        user.id, body.get_meta("project_id"), app_id=app_id,
                        max_age_days=recall_max_age_days)
                    if dossier and (dossier.get("meetings")
                                    or dossier.get("facts")
                                    or dossier.get("action_items")):
                        block = cq.format_dossier(dossier)
                        if "{{context_quilt}}" in body.system_prompt:
                            new_system = body.system_prompt.replace(
                                "{{context_quilt}}",
                                f"{RECALL_USE_GUARD}\n{block}")
                        else:
                            # APPEND, not prepend. See the note on the other
                            # injection path below: the envelope declares
                            # context_quilt last in the system block, and
                            # prepending put a per-turn block ahead of
                            # everything we want cached.
                            # The guard leads the block here for the same
                            # reason it does there: a dossier is scoped to
                            # one project, which bounds WHAT can be recited
                            # but not WHETHER reciting happens.
                            new_system = (body.system_prompt + "\n\n"
                                          + RECALL_USE_GUARD + "\n" + block)
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
                timeout_ms=recall_timeout_ms,
            )
            result["cq_result"] = cq_result

            # Communication style goes in BEFORE recall, and the order is
            # the whole point (measured 2026-08-17).
            #
            # Recall is appended LAST on the chat surfaces and is per-turn
            # volatile, so the Anthropic adapter deliberately leaves it in
            # the uncached tail: caching it would write an entry covering
            # prefix+recall that changes every turn and can never be read
            # back. That optimisation only holds while recall is the last
            # thing in the prompt. Appending style AFTER it made the tail
            # non-empty, which flipped recall to cached and bought exactly
            # the wasted per-turn write the adapter is written to avoid.
            #
            # Injected first, the style rides inside the CACHED PREFIX
            # instead: it keeps full system-prompt steering weight, it is
            # cached rather than re-sent, and recall goes back to being
            # last. Verified on the built blocks, not inferred: prefix
            # grows from 33 to 142 chars cached and the recall breakpoint
            # disappears.
            #
            # (Templates carrying an explicit {{context_quilt}} placeholder
            # would still land recall mid-prompt, but the chat surfaces do
            # not emit one, and they are the only modes that get style.)
            if cq_result.get("communication_style") and body.get_meta("prompt_mode") in (
                "ProjectChat", "PostMeetingChat"
            ):
                body = body.model_copy(update={
                    "system_prompt": body.system_prompt + f"\n\n{cq_result['communication_style']}"
                })

            if cq_result.get("context"):
                body = _inject_recall_block(body, cq_result["context"])

            body = _fire_memory_edits(body)

        elif feature_state == "teaser" and "context_quilt" not in skip_teasers:
            # Teaser: recall for metadata only, don't inject
            cq_result = await cq.recall(
                app_id=app_id,
                user_id=user.id,
                text=body.user_content,
                metadata=cq_metadata or None,
                subscription_tier=user.effective_tier,
                timeout_ms=recall_timeout_ms,
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
        recall_max_age_days: int | None = None,
        recall_timeout_ms: int | None = None,
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
        _apply_recall_window(cq_metadata, recall_max_age_days)
        cq_result = await cq.recall(
            app_id=app_id,
            user_id=user.id,
            text=body.user_content,
            metadata=cq_metadata,
            subscription_tier=user.effective_tier,
            timeout_ms=recall_timeout_ms,
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

        _detect_recital(body, response)

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


# `[ \t]+`, NOT `\s+`: a multi-word name must not span a NEWLINE. With
# `\s+` this welded the last name on one line to the first on the next
# ("CTS  \nDon" as a single term), which then matched nothing in any
# response and silently cost the detector both names.
_PROPER_NOUN = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}(?:[ \t]+[A-Z][A-Za-z0-9]{2,})*")

# Words of overlap that count as reciting a line. Long enough that ordinary
# phrasing does not collide, short enough to catch a clause lifted out of a
# longer patch line.
_SHINGLE = 5
_LEADING_TAG = re.compile(r"^\s*\[[a-z_]+\]\s*", re.I)


def _norm_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower()).split()


def _shingles(words: list[str], n: int = _SHINGLE) -> set[tuple]:
    if len(words) < n:
        return {tuple(words)} if len(words) >= 3 else set()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _recited_lines(block: str, material: str, answer: str) -> int:
    """How many injected LINES were echoed back, names or not.

    CQ's correction, and it is the right one: a name is a proxy for the harm,
    not the harm. The original incident leaked an overdue customer
    COMMITMENT. A turn that recites "ship the API gateway by Friday" carries
    another project's commitment into an unrelated meeting with no proper
    noun anywhere in it, and a name-keyed detector calls that clean.

    This does not need NER and is not a heuristic, because we know exactly
    what text we injected. Compare the answer against the BLOCK'S OWN LINES,
    with the same not-in-the-material subtraction: an overlap the model could
    have got from the transcript is not a recital.
    """
    mat = set()
    for m_sh in (_shingles(_norm_words(material)),):
        mat |= m_sh
    ans_words = _norm_words(answer)
    ans = _shingles(ans_words)
    if not ans:
        return 0

    hits = 0
    for raw in _strip_chrome(block).splitlines():
        line = _LEADING_TAG.sub("", raw).strip()
        if not line:
            continue
        line_sh = _shingles(_norm_words(line))
        if not line_sh:
            continue
        # Present in the answer, and NOT available from the material.
        if line_sh & ans and (line_sh & ans) - mat:
            hits += 1
    return hits


# CQ's block CHROME, stripped by SHAPE rather than by vocabulary.
#
# The first version of this was a hardcoded word list (OVERDUE, Projects,
# People, ...). CQ pointed out that is a contract with one carrier living at
# the wrong hop: their section labels are LOCALIZED per metadata.locale
# across five tables today, while their flat markers stay English on
# purpose. So an English word list fails ASYMMETRICALLY. It looks perfect on
# English traffic and goes blind the moment a French or Japanese caller
# arrives, and nothing anywhere fails when they add a locale or rename a
# label.
#
# The SHAPE is stable even when the vocabulary is not: markers are
# parenthesised, and a section label is a short leading `Word:` at the start
# of a line. Stripping on that is immune to their locale table and to
# anything they add later, with no coordination between us.
#
# Cost, stated because it is real: content inside parentheses stops being a
# NAME signal, so "CTS (Ticket Creation System project)" contributes CTS but
# not the expansion. Acceptable, because the line predicate still covers a
# recital of that whole line, and the two signals are complementary.
_CHROME_PAREN = re.compile(r"\([^)]*\)")
_CHROME_LABEL = re.compile(r"^[ \t]*[^\s:][^:\n]{0,28}:[ \t]+", re.M)


def _strip_chrome(text: str) -> str:
    """Remove CQ's rendering furniture, leaving the content it wraps."""
    return _CHROME_LABEL.sub("", _CHROME_PAREN.sub(" ", text or ""))


def _recital_terms(block: str, material: str) -> set[str]:
    """Names that came from the CONTEXT and not from the material.

    This is the guard's own rule made checkable: the prompt forbids naming
    what appears in the context but NOT in the material itself, so the
    detector tests exactly that predicate rather than a proxy for it. A name
    in both is legitimate and must not fire; in a genuinely-ABM meeting
    "CTS" belongs in the summary.
    """
    mat = material or ""
    out = set()
    for m in _PROPER_NOUN.findall(_strip_chrome(block)):
        term = m.strip()
        if len(term) < 3:
            continue
        if re.search(rf"\b{re.escape(term)}\b", mat, re.I):
            continue  # grounded in the material; not a recital
        out.add(term)
    return out


def _detect_recital(body: ChatRequest, response: ChatResponse) -> None:
    """Did the answer name context-only material? Log it if so.

    The reason this exists rather than another prompt tweak: #825 and #828
    were both verified by replaying ONE turn twenty times, and 0/20 leaves a
    95% upper bound near 14% conditional on the trigger. A sample that size
    cannot see a small residual. This is continuous and phrasing-independent,
    so it catches a recital produced by wording nobody anticipated, which is
    the whole failure mode: #825's prohibition was defeated by the model
    inventing a politer way to say the same thing.

    PRIVACY: the terms are a customer's people and projects, so they are
    NEVER logged. Only the count, the lengths and the request id go out;
    anyone investigating pulls the stored row, which already holds the text.
    Logging the names would turn a leak detector into a second leak.
    """
    try:
        block = (body.metadata or {}).get("cq_recall_block")
        if not block:
            return
        # `.text`, NOT `.content`. The first version of this read
        # `.content`, which ChatResponse does not have, so the detector
        # would have returned early on EVERY turn and logged nothing,
        # while looking installed and passing any test that only
        # checked it did not crash. A silent detector is worse than no
        # detector: it answers "no recitals found" forever.
        text = getattr(response, "text", None) or ""
        if not text:
            return
        material = body.user_content or ""
        terms = _recital_terms(block, material)
        hits = sorted(t for t in terms
                      if re.search(rf"\b{re.escape(t)}\b", text, re.I))
        # Names are ONE signal inside the predicate, not the predicate. A
        # recited commitment with no proper noun in it is the same
        # confidentiality failure and the name check reports it clean.
        line_hits = _recited_lines(block, material, text)
        if not hits and not line_hits:
            return
        logger.warning(
            "cq_recital_detected",
            extra={
                "call_type": body.get_meta("call_type"),
                "term_count": len(hits),
                "line_count": line_hits,
                "material_chars": len(body.user_content or ""),
                "block_chars": len(block),
                "response_chars": len(text),
                # Lengths only. See the docstring: the terms themselves are
                # the customer data this detector exists to protect.
                "term_lengths": [len(t) for t in hits[:10]],
            },
        )
    except Exception:
        # A detector must never break the turn it is watching.
        logger.exception("cq_recital_detector_failed")


# Lanes where user_content IS the material to be worked on. ONLY these are
# gated. Chat is deliberately absent and must stay absent: there the content
# is a QUESTION, "what did we decide about the promotion?" is forty
# characters, and the recall block is the intended answer source. A floor
# calibrated on transcripts would switch Meeting Memory off in chat entirely.
#
# An unrecognised call_type is NOT gated. Fail open: a new lane losing recall
# silently is a worse first failure than a new lane keeping it, and #828
# already covers the recital behaviour in the model.
_MATERIAL_LANES = {"summary", "analysis"}


def _material_below_floor(body: ChatRequest) -> bool:
    """Is this a material lane whose material is too thin to work on?

    MEASURED on the incident turn (request_id 4b0617fb7270):

        material (the transcript) :  608 chars
        injected context block     : 2261 chars
        context / material         :  3.7x

    The model was asked to summarize 31 seconds of podcast and handed a
    block almost four times that size containing a customer's people,
    overdue commitments and decisions. It read that back, which is what a
    model does when the context IS the only substantial content in the room.

    #825 and #828 both act on the model AFTER it is in that state. This
    declines to create the state. The two are independent layers, which
    matters because 0/20 on the deployed guard leaves a 95% upper bound
    near 14% conditional on the trigger; that is a residual worth a second
    mitigation rather than a rounding error.

    Skipping here also skips the CQ call itself, so a turn that should not
    have recalled no longer perturbs `last_accessed_at` decay on CQ's side.

    Floor is served config, so it moves without a deploy. Default 900:
    the incident sits at 608, while the smallest material that produced a
    genuine summary in the sampled traffic was 963.
    """
    if (body.get_meta("call_type") or "") not in _MATERIAL_LANES:
        return False
    floor = _material_floor_chars()
    if floor <= 0:
        return False
    return len(body.user_content or "") < floor


def _material_floor_chars() -> int:
    try:
        return max(0, int(getattr(
            get_settings(), "cq_recall_min_material_chars", 900)))
    except (TypeError, ValueError):
        return 900


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


def _apply_recall_window(cq_metadata: dict[str, Any], max_age_days: int | None) -> None:
    """Plus recall window (2026-08-21): `metadata.max_age_days` on every
    /v1/recall leg. Set server-side from the tier dial AFTER the request
    composition, like recall_scope and subscription_tier, so a client can
    neither widen nor narrow its own window. CQ's contract: absent means
    no window, so unlimited tiers send nothing rather than a sentinel."""
    if isinstance(max_age_days, int) and not isinstance(max_age_days, bool) and max_age_days >= 1:
        cq_metadata["max_age_days"] = max_age_days
    else:
        cq_metadata.pop("max_age_days", None)


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

    Guard: RECALL_USE_GUARD goes immediately BEFORE the block on both
    paths, so a turn that cannot use the context refuses without reciting
    it. See the constant for why before and not after.

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
        new_system = base.replace(
            "{{context_quilt}}", f"{RECALL_USE_GUARD}\n{cq_context}")
    else:
        new_system = (
            f"{base}\n\n{RECALL_USE_GUARD}\n"
            f"[CONTEXT FROM PREVIOUS MEETINGS]\n{cq_context}"
        )
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
