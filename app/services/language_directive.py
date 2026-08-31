"""Transcript language, stated by the client, turned into a served line.

2026-08-21: a Spanish meeting recorded through SS's own language picker got
an English refusal ("I would need a clear, legible transcript in English")
where its summary should have been. The served summary prompts carried no
language rule at all, and nothing on the wire told the model which
language was expected, so it inferred and refused. Two fixes, layered:

1. The served recipe now says "write in the language of the transcript"
   (config/remote/protected-prompts*.json), so inference has a rule.
2. The client may STATE the language instead of leaving it to inference,
   which is exactly what failed: `metadata.transcript_language`, a BCP-47
   tag. NOT `metadata.language`: that key already rides capture and means
   the DEVICE language (CQ writes memory in it). When present, GP
   appends the line below to the system prompt, server-side, so placement
   is ours (prompt composition doctrine) and a client never hardcodes it.

The line is phrased for every transcript-bearing lane, including chat:
a user who writes in another language is answered in theirs.
"""
from __future__ import annotations

import re

_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def transcript_language(tag) -> str | None:
    """A plausible BCP-47 tag, or None. Never raises: a bad value is the
    same as no value, because a refused turn is the failure we are
    removing, not one we want to add."""
    if not isinstance(tag, str):
        return None
    tag = tag.strip()
    return tag if tag and _TAG.match(tag) else None


def language_line(tag: str) -> str:
    return (
        f"TRANSCRIPT LANGUAGE: {tag}. The meeting was held in this language. "
        "Write your response in it, unless the user writes to you in a "
        "different language, in which case answer in theirs. If the "
        "transcript is noisy or partial, work with what can be understood "
        "and note the gaps briefly; never refuse and never ask for a "
        "transcript in another language."
    )


def append_language_line(system_prompt: str | None, tag) -> str | None:
    """System prompt with the directive appended, or unchanged when the
    client stated no usable language."""
    lang = transcript_language(tag)
    if not lang:
        return system_prompt
    base = (system_prompt or "").rstrip()
    line = language_line(lang)
    return f"{base}\n\n{line}" if base else line


def resolve_report_locale(stated_language, accept_language_header: str | None) -> str | None:
    """Report lane: the language the meeting was held in, if the client
    stated it, else the device locale from Accept-Language. The device
    locale says what the UI is in, not what the people in the room spoke."""
    lang = transcript_language(stated_language)
    if lang:
        # Primary subtag only: report-strings.{locale} and
        # canned-report.{locale} are keyed "es", "ja", "fr", and the
        # locale directive names a language, not a region. SS sends the
        # RESOLVED locale ("es-US", the model that actually transcribed),
        # which is the right value on the wire and must not miss the
        # bundle lookups here. The chat line keeps the full tag.
        return lang.split("-")[0].lower()
    from app.routers.config import _parse_accept_language
    return _parse_accept_language(accept_language_header)
