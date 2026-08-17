"""Suggested meeting titles, derived from the auto summary.

WHY THIS EXISTS. A meeting title is written by exactly two things: post
session analysis, or the full report. The auto summary never wrote one.
And a meeting long enough to earn a report has its analysis deliberately
SKIPPED, on the reasoning that the report will supply title, sentiment,
urgency and tags. So when a report does not arrive, that meeting gets
neither, and nothing repairs it afterwards.

Observed 2026-08-17: a real 23 minute meeting whose card read "Meeting
Summary" while the summary under it opened "The team reviewed open QA
issues, latency metrics, and Service Now form availability problems".
The material for a title was plainly there. The client had derived its
display title from the summary's first line, which was a heading the
model wrote, which is the fragile thing this replaces.

WHY A SEPARATE CALL rather than asking the summary for a title. We do
not own the summary prompt; the client assembles it. We could append an
instruction and parse a sentinel line back out, but that pollutes the
summary text, which is stored and later read by the report builder, and
it leaves the field's existence at the mercy of the model phrasing its
heading differently one day. That is the same class of fragility the
client just got burned by. A small dedicated call with its own
validation is cheap and its failure mode is a clean absence.

ABSENT BEATS GENERIC, and this is the one way the feature could make
things worse. The client treats a SERVED title as authoritative and
skips its own fallback entirely, so a generic title we send is a
generic title that renders. They can always fall back to a date; they
cannot recover from us handing them "Weekly Sync". So the blocklist
below runs on OUR side after the model answers, rather than living only
in the prompt where compliance is optional.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

_TITLE_MODEL = "claude-haiku-4-5-20251001"

# Prompt modes whose response should carry a suggested title.
TITLE_PROMPT_MODES = ("AutoSummary", "DeltaSummary", "SummaryConsolidation")

MAX_TITLE_CHARS = 60

# Words that carry no information about WHICH meeting this was. A title
# made only of these is a label, not a name. Kept as parts rather than
# whole phrases so "Weekly Team Sync Meeting" collapses to nothing and is
# rejected, the same way the client's own filter now works.
_FILLER = {
    "meeting", "meetings", "summary", "summaries", "notes", "note",
    "recap", "review", "session", "call", "sync", "syncing", "standup",
    "stand", "up", "checkin", "check", "in", "catch", "catchup",
    "update", "updates", "status", "discussion", "discussions",
    "conversation", "chat", "team", "weekly", "daily", "monthly",
    "quarterly", "biweekly", "morning", "afternoon", "general",
    "regular", "routine", "the", "a", "an", "and", "or", "of", "for",
    "with", "on", "project", "planning", "plan", "touchpoint", "touch",
    "base", "1", "one", "1on1", "agenda", "minutes", "transcript",
}

_SYSTEM = (
    "You name meetings. You are given a summary of one meeting and you "
    "return a short NAME for it.\n\n"
    "Return JSON only: {\"title\": \"...\"} or {\"title\": null}.\n\n"
    "A good title is what someone would call this meeting to tell it "
    "apart from every other meeting in a list. Name the SUBJECT: the "
    "system, team, customer, decision or problem it was actually about. "
    "Two to five words. Title Case.\n\n"
    "Good: Latency and QA Blockers. Cigna Demo Prep. Pricing Model "
    "Rework. Hardware Form Testing.\n\n"
    "Bad, and never acceptable: Meeting Summary. Status Update. Weekly "
    "Sync. Team Meeting. Project Discussion. Any name that would fit a "
    "different meeting equally well.\n\n"
    "Do not summarise the summary. A title is a name, not a sentence, "
    "and it never contains a verb phrase describing what the team did.\n\n"
    "If the summary has no distinguishing subject, return null. A "
    "missing title is better than a generic one, because something else "
    "supplies the fallback and a wrong name sticks."
)


def _normalise(title: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", title.lower()) if w]


def is_generic(title: str) -> bool:
    """True when nothing in the title identifies WHICH meeting it was.

    Runs after the model, not instead of it. Compliance with a prompt is
    a hope; this is the guarantee.
    """
    words = _normalise(title)
    if not words:
        return True
    return all(w in _FILLER for w in words)


def clean_title(raw: object) -> str | None:
    """Serve it, or serve nothing. Never serve a label."""
    if not isinstance(raw, str):
        return None
    title = " ".join(raw.split()).strip(" .:-–—\"'")
    if not title or len(title) > MAX_TITLE_CHARS:
        return None
    if is_generic(title):
        logger.info("suggested_title rejected as generic: %r", title)
        return None
    return title


async def suggest_title(provider_router, summary_text: str,
                        on_subcall=None) -> str | None:
    """Name the meeting from its summary. None on ANY failure.

    Fail-open by design: the client has a date to fall back to, so a
    missing title costs a little and a wrong one costs more.
    """
    if not summary_text or len(summary_text.strip()) < 40:
        # Nothing to name it from. Do not spend a call to learn that.
        return None

    from app.models.chat import ChatRequest

    request = ChatRequest(
        provider="anthropic",
        model=_TITLE_MODEL,
        system_prompt=_SYSTEM,
        user_content=summary_text[:4000],
        max_tokens=100,
        temperature=0.0,
        call_type="meeting_title",
        prompt_mode="MeetingTitle",
    )
    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            provider_router.route(request), timeout=10.0)
        if on_subcall is not None:
            await on_subcall(request, response,
                             int((time.monotonic() - start) * 1000))
        txt = response.text or ""
        parsed = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        return clean_title(parsed.get("title"))
    except Exception:
        logger.info("suggested_title unavailable", exc_info=True)
        return None
