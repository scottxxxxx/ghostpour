"""The N-400 field catalog, held by GP (auditor, 2026-11-13).

Every field id and every choice field's options are the same for every
applicant, so they ride in the cached system prompt as an appendix instead of
in each request. Built from the client's own form definition by
scripts/build_n400_field_catalog.py, and checked against build 215's wire
`choice_fields` before it shipped: 130 of 130 fields, same options, same order.

Old builds (215, 216) still send `choice_fields`; when they do, theirs is
used, because it is what that build files against. A build that stops
sending it gets this copy, so the option guard and the evidence check keep
covering every choice field rather than falling back to the agenda's 29
yes/no gates.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "resources" / "n400_field_catalog.json"
APPENDIX_HEAD = "FIELD CATALOG"
# The 96 field offices are the PDF's dropdown strings (" Alaska - USCIS ..."):
# a leading space and a spaced hyphen each, 3.8k chars of exactly the
# punctuation the no-dash rule forbids. The app matches what she says to an
# office on screen (N400Helper FieldOfficeChoice.swift), so the lane never
# needs them; the guard still checks against the full list in the JSON.
DESCRIBED_NOT_LISTED = {
    "p1.interview_field_office":
        "one of 96 USCIS field offices; the app matches what she says to the list on screen",
}


@lru_cache(maxsize=1)
def _fields() -> tuple[dict, ...]:
    return tuple(json.loads(_PATH.read_text())["fields"])


def choice_fields() -> dict[str, list[str]]:
    """field_id -> options, the shape of the client's metadata.choice_fields."""
    return {f["field_id"]: list(f["options"]) for f in _fields() if f.get("options")}


def field_ids() -> list[str]:
    return [f["field_id"] for f in _fields()]


def choice_fields_for(meta_value) -> dict:
    """The client's map when it sent a usable one, else GP's own."""
    from app.services.n400_interviewer_guard import catalogue_field_options
    # Usable means the guard can read at least one field from it; a map it
    # would clean to nothing gets GP's copy, not the agenda's 29 gates.
    if catalogue_field_options(meta_value):
        return meta_value
    return choice_fields()


def render_appendix() -> str:
    """One line per part: ids in catalog order, a choice field's options in
    parentheses after it. The exact text that sits in the system prompt."""
    parts: dict[int, list[str]] = {}
    for f in _fields():
        cell = f["field_id"]
        if f["field_id"] in DESCRIBED_NOT_LISTED:
            cell += " (" + DESCRIBED_NOT_LISTED[f["field_id"]] + ")"
        elif f.get("options"):
            cell += " (" + "|".join(f["options"]) + ")"
        parts.setdefault(f["part"], []).append(cell)
    lines = [
        APPENDIX_HEAD,
        "Every field id on this form, by part, in form order. A field with options lists them in "
        "parentheses, separated by |, and its value is always exactly one of them.",
        "Everywhere these instructions say VOLUNTEER FIELDS and the variable came empty, the list "
        "meant is this catalog: every field of every part except Part 9 (Part 9's only while the "
        "first AGENDA line is in Part 9), leaving out any field KNOWN FACTS already holds a value "
        "for. A deferral is not a value, so a deferred field stays on the list.",
    ]
    for part in sorted(parts):
        lines.append("Part %d: %s" % (part, ", ".join(parts[part])))
    return "\n".join(lines)
