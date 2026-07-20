"""Native action extraction (SS feature request, 2026-07-19).

Extends the live-session savesToReminders flow to the chat surfaces:
when a Project Chat / Meeting Chat ask is action-items-shaped, the
response envelope carries an OPTIONAL additive `native_action` block —
kind "reminders" plus a structured items array — and the client renders
a one-tap "Add to Reminders" chip. Display text stays clean: the items
are extracted from the finished answer by a cheap post-answer sub-call,
never by sentinel blocks in the visible text.

Contract (docs/wire-contracts/native-actions.md):
    "native_action": {
        "kind": "reminders",
        "items": [
            {"title": str,                # required, 1-200 chars
             "due": "YYYY-MM-DD" | "YYYY-MM-DDTHH:MM" | absent,
             "owner": str | absent}       # display name, <=80 chars
        ]                                 # 1-20 items
    }
Absent by default; absent on every failure (fail-open); never present
alongside an armed generation turn. Future kinds (e.g. "email_draft")
extend the same block additively.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

_EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"

_SURFACES = {"ProjectChat", "PostMeetingChat"}

# Deterministic prefilter — the extractor sub-call only runs when the
# question portion carries task vocabulary, same recall-biased pattern
# as the generation-intent prefilter.
_ACTION_HINTS = (
    "action item", "action-item", "task list", "to-do", "todo",
    "to do list", "my tasks", "follow-up", "follow ups", "followups",
    "next steps", "reminders", "what do i need to do",
    "what do i have to do", "assigned to me", "my commitments",
    "open items", "deliverables",
)

_DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")

_EXTRACTOR_SYSTEM = (
    "You extract actionable to-do items from an assistant's answer so "
    "they can be saved as reminders. Use ONLY items the answer actually "
    "states; never invent tasks, dates, or owners. Return ONLY a JSON "
    "object: {\"items\": [{\"title\": string (imperative, under 200 "
    "chars), \"due\": string (YYYY-MM-DD, or YYYY-MM-DDTHH:MM when the "
    "answer gives a time) or null, \"owner\": string (person's name) or "
    "null}]}. Include at most 20 items. If the answer contains no "
    "actionable items, return {\"items\": []}. No markdown. Never use "
    "em dashes anywhere in your output."
)


def native_actions_enabled(remote_configs: dict) -> bool:
    """Served flag: client-config.native_actions.enabled. Absent = off,
    so the block can be dark-shipped and flipped without a deploy."""
    cfg = (remote_configs or {}).get("client-config") or {}
    block = cfg.get("native_actions")
    return bool(isinstance(block, dict) and block.get("enabled"))


def looks_like_action_items_ask(text: str) -> bool:
    tail = (text or "")[-2000:].lower()
    return any(h in tail for h in _ACTION_HINTS)


def _validate_items(raw) -> list[dict] | None:
    if not isinstance(raw, list):
        return None
    items: list[dict] = []
    for entry in raw[:20]:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        item: dict = {"title": title.strip()[:200]}
        due = entry.get("due")
        if isinstance(due, str) and _DUE_RE.match(due.strip()):
            item["due"] = due.strip()
        owner = entry.get("owner")
        if isinstance(owner, str) and owner.strip():
            item["owner"] = owner.strip()[:80]
        items.append(item)
    return items or None


async def maybe_extract_native_action(
    provider_router,
    remote_configs: dict,
    *,
    prompt_mode: str | None,
    question: str,
    answer_text: str,
    on_subcall=None,
) -> dict | None:
    """The full gate + extraction: returns the additive `native_action`
    block or None. Fail-open on every path — a missing block is always
    valid wire."""
    if prompt_mode not in _SURFACES:
        return None
    if not native_actions_enabled(remote_configs):
        return None
    if not answer_text or not answer_text.strip():
        return None
    from app.services.document_generation import _question_portion
    if not looks_like_action_items_ask(_question_portion(question)):
        return None

    import time as _time

    from app.models.chat import ChatRequest
    request = ChatRequest(
        provider="anthropic",
        model=_EXTRACTOR_MODEL,
        system_prompt=_EXTRACTOR_SYSTEM,
        user_content=(
            "QUESTION:\n" + _question_portion(question)[-1000:]
            + "\n\nANSWER:\n" + answer_text[:12000]
        ),
        max_tokens=1500,
        temperature=0.0,
        call_type="native_action_extract",
        prompt_mode="NativeActionExtract",
    )
    start = _time.monotonic()
    try:
        response = await asyncio.wait_for(provider_router.route(request), timeout=12.0)
        elapsed_ms = int((_time.monotonic() - start) * 1000)
        if on_subcall is not None:
            await on_subcall(request, response, elapsed_ms)
        txt = response.text or ""
        parsed = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        items = _validate_items(parsed.get("items"))
        if not items:
            return None
        return {"kind": "reminders", "items": items}
    except Exception as e:  # noqa: BLE001
        logger.info("native action extraction failed open: %s", e)
        return None
