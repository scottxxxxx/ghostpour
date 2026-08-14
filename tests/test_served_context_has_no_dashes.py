"""No em or en dashes in anything GP composes into a model's context.

Scott's standing rule bans them in served LLM output, and
`app/services/text_hygiene.py` already scrubs the final answer on the
conversational surfaces. That backstop exists because, in its own words,
"models copy the punctuation they see, and the injected context is
dash-heavy". What it could not fix is that GP was writing dashes into
that context ITSELF: the project memory dossier headed every meeting
`## Meeting 1 of 21 — 2026-08-11`, and the meeting report schema said
`"string — short label"` thirty-two times, in the same turn that asks
for prose a person reads.

So the instruction and the example contradicted each other, and the
example is the one the model imitates. These tests guard the composers,
which is upstream of the scrubber and cheaper than it.

Log lines are deliberately out of scope: nothing reads them but us.
"""

from __future__ import annotations

import pytest

DASHES = ("—", "–")  # em, en


def _no_dash(text: str, what: str) -> None:
    for d in DASHES:
        assert d not in text, (
            f"{what} carries a {'em' if d == chr(0x2014) else 'en'} dash. "
            "The model imitates the punctuation it is shown.")


def test_the_project_memory_dossier_is_clean():
    from app.services.context_quilt import format_dossier
    block = format_dossier({
        "meetings": [{"patches": [
            {"patch_id": "p1", "created_at": "2026-07-14T10:00:00Z",
             "content": "Ada owns the migration", "type": "fact"},
        ]}],
        "action_items": [
            {"patch_id": "p2", "content": "Send the plan", "type": "action_item"},
        ],
    })
    _no_dash(block, "the dossier block")
    # the heading still carries its date, just not with a dash
    assert "## Meeting 1 of 1 (2026-07-14)" in block
    assert "PROJECT MEMORY DOSSIER" in block


def test_the_meeting_report_prompt_is_clean_and_says_the_rule():
    from app.services.meeting_report import (
        REPORT_SYSTEM_PROMPT,
        REPORT_USER_TEMPLATE,
        _LOCALE_DIRECTIVE,
    )
    for name, text in (("the report system prompt", REPORT_SYSTEM_PROMPT),
                       ("the report user template", REPORT_USER_TEMPLATE),
                       ("the locale directive", _LOCALE_DIRECTIVE)):
        _no_dash(text, name)
    # This prompt had no dash rule at all while writing narrative fields a
    # person reads, so showing it clean examples was only half the fix.
    assert "without em or en dashes" in REPORT_SYSTEM_PROMPT
    assert "spaced hyphen" in REPORT_SYSTEM_PROMPT


def test_the_document_framing_is_clean():
    from app.services.documents import _FRAMING_PREAMBLE
    _no_dash(_FRAMING_PREAMBLE, "the attached-document preamble")


@pytest.mark.parametrize("locale", ["es-US", "ja", "fr-FR"])
def test_the_locale_directive_is_clean(locale):
    from app.services.locale_injection import language_directive
    _no_dash(language_directive(locale) or "", f"the {locale} directive")


def test_the_gantt_extraction_prompts_stay_clean():
    """These already carried the ban; keep them in the same net so a future
    edit trips one guard rather than none."""
    from app.services.doc_templates import (
        _GANTT_DETAILED_SCHEMA_PROMPT,
        _GANTT_SCHEMA_PROMPT,
    )
    _no_dash(_GANTT_SCHEMA_PROMPT, "the gantt prompt")
    _no_dash(_GANTT_DETAILED_SCHEMA_PROMPT, "the detailed gantt prompt")
