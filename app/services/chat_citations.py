"""Meeting moment citations in SS Project Chat and Meeting Chat (2026-11-19).

SS adds a short ref to each meeting header ("[ref m6D8A]") and a time marker
to each transcript speaker turn ("[12:34] Dana: ..."). The served rule in
protected-prompts `chatCitationInstruction` tells the model to cite
"[m6D8A 12:34]"; the client turns that into a link that plays the meeting
from that moment.

GP appends the rule only on the two chat surfaces AND only when the request
actually carries a ref, so an older build (or any send with no refs) never
gets a rule that would invite citing refs that do not exist.
"""
from __future__ import annotations

import re

SURFACES = frozenset({"ProjectChat", "PostMeetingChat"})
_REF = re.compile(r"\[ref m[0-9A-F]{4,8}\]")


def citation_rule(remote_configs: dict, locale: str | None = None) -> str | None:
    """The served rule text, locale variant first, then the base config."""
    for slug in ((f"protected-prompts.{locale}",) if locale else ()) + ("protected-prompts",):
        text = (remote_configs.get(slug) or {}).get("chatCitationInstruction")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def append_citation_rule(system_prompt: str, user_content: str, prompt_mode: str | None,
                         remote_configs: dict, locale: str | None = None) -> str:
    if prompt_mode not in SURFACES:
        return system_prompt
    if not (_REF.search(system_prompt or "") or _REF.search(user_content or "")):
        return system_prompt
    rule = citation_rule(remote_configs, locale)
    if not rule or rule in (system_prompt or ""):
        return system_prompt
    base = (system_prompt or "").rstrip()
    return base + "\n\n" + rule if base else rule
