"""The N-400 field catalog, held by GP (auditor, 2026-11-13).

Every choice field's options are the same for every applicant. GP keeps a
copy (no tokens: it never reaches a prompt) behind the option marker and
Jev's evidence check. Built from the client's own form definition by
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

