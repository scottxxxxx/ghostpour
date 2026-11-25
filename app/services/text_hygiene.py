"""Dash hygiene for GhostPour-served LLM chat output.

Scott's standing rule bans em and en dashes in ALL served LLM output.
The served prompts carry the ban, but models copy the punctuation they
see, and the injected context (meeting summaries, project history,
earlier messages) is dash-heavy; live 2026-08-12 a Project Chat answer
shipped several em dashes past the template ban. This is the
belt-and-suspenders backstop: mechanical and conservative, applied
server-side to the final prose answer on the conversational surfaces
only, never to machine-parsed JSON turns and never to template
extraction turns (the receipts sheet quotes meeting lines verbatim).

Substitution follows the house rule ("use a comma, colon, or
parentheses instead"): the mechanical default is the comma, with two
carve-outs where a dash is not punctuation at all: a dash between
digits is a range and becomes a hyphen, and a dash opening a list line
is a bullet and becomes a hyphen.
"""

from __future__ import annotations

import re

_EM_EN = "–—"
# Arrow glyphs are the same family (2026-09-05, a chat answer summarising a
# docx joined two dates with a right arrow): a span in speech is "to".
_ARROWS = re.compile(r"[ \t]*[\u2192\u2190\u21d2\u2794\u279c][ \t]*")
_RANGE = re.compile(rf"(?<=\d)[ \t]*[{_EM_EN}][ \t]*(?=\d)")
# A TIGHT en dash between two words is a range too ("October–December",
# "Q3–Q4", "9am–5pm") and reads as "to" in speech and in a file; an em
# dash or a spaced dash between words is an aside and becomes a comma.
_WORD_RANGE = re.compile(r"(?<=[A-Za-z0-9])\u2013(?=[A-Za-z0-9])")
_BULLET = re.compile(rf"(?m)^([ \t]*)[{_EM_EN}][ \t]*(?=\S)")
_ASIDE = re.compile(rf"[ \t]*[{_EM_EN}]+[ \t]*")
_TRAILING = re.compile(r",[ \t]+(?=\r?\n|$)")


def normalize_dashes(text: str) -> str:
    """Rewrite em and en dashes per the house rule; no-op on clean text.

    Order matters: ranges first (7-10 stays a span), then line-leading
    bullets, then every remaining dash reads as an aside or break and
    becomes a comma. Runs of dashes collapse to one comma; a dash left
    hanging at line end becomes a bare comma."""
    if not text or not (any(d in text for d in _EM_EN) or _ARROWS.search(text)):
        return text
    text = _ARROWS.sub(" to ", text)
    text = _RANGE.sub("-", text)
    text = _WORD_RANGE.sub(" to ", text)
    text = _BULLET.sub(r"\1- ", text)
    text = _ASIDE.sub(", ", text)
    return _TRAILING.sub(",", text)


# The fixed "small talk only" bullet each locale's summary prompt names.
# Live 2026-11-20 on Haiku 4.5: Spanish sometimes printed it ABOVE real work
# bullets, contradicting itself. It is only meaningful as the whole summary.
SMALL_TALK_BULLETS = (
    "- Small talk so far, no work discussed yet",
    "- Por ahora solo charla informal, aún no se habló de trabajo",
    "- Que des banalités pour l'instant, pas encore de travail abordé",
    "- 今のところ雑談のみで、仕事の話はまだ出ていない",
    "- Por enquanto só conversa informal, nenhum assunto de trabalho ainda",
)


def drop_stray_small_talk_bullet(text: str) -> str:
    """Remove the small-talk bullet when any other bullet is present."""
    if not text:
        return text
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if l.strip() in SMALL_TALK_BULLETS]
    if not hits:
        return text
    others = [l for i, l in enumerate(lines)
              if i not in hits and l.lstrip().startswith(("- ", "* "))]
    if not others:
        return text
    kept = [l for i, l in enumerate(lines) if i not in hits]
    while kept and not kept[0].strip():
        kept.pop(0)
    return "\n".join(kept)
