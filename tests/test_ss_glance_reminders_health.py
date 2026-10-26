"""SS glance cues, the served Reminders paragraph, and the summary health rule
(2026-10-26).

glanceInstruction: Scott's Glanceable Speaking Guide (10-25), "one glance,
one idea, eyes back to the meeting". The client appends it to the mode or
follow-up instruction and parses <cues>...</cues> (GlanceCues.swift, SS Dev).
On Sonnet 5 the block came first in 42 of 42 probe calls
(qa/glance_cues_probe.py); the square bracket and gap card rule took bracket cards
from 11 of 14 to 1 of 14 on Sonnet 5.

remindersInstruction replaces the paragraph SS hardcoded in SessionManager.
ActionItemStore finds the two sentinels by plain substring, displays only
the text BEFORE the start marker, and strips one "- ", "* ", "• " or "‣ "
prefix, so the sentinels are byte identical and the block stays last.

Summaries leave out health details (Scott, 2026-10-25).
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "config" / "remote"
LOCALES = {"en": "protected-prompts.json", "es": "protected-prompts.es.json",
           "fr": "protected-prompts.fr.json", "ja": "protected-prompts.ja.json"}

NO_BRACKET = {"en": "No part of a card ever contains a square bracket",
              "es": "Ninguna parte de una tarjeta contiene nunca un corchete",
              "fr": "Aucune partie d'une carte ne contient jamais de crochet",
              "ja": "カードのどの部分にも角かっこを決して入れないでください。"}
HEALTH = {"en": "Leave out health details:", "es": "Omite los detalles de salud:",
          "fr": "Omettez les détails de santé :", "ja": "健康に関する詳細は書かないでください"}
DELTA_REMOVE = {"en": "Remove any such detail already in the previous summary.",
                "es": "Elimina cualquier detalle de ese tipo que ya esté en el resumen anterior.",
                "fr": "Supprimez tout détail de ce type déjà présent dans le résumé précédent.",
                "ja": "前回の要約にすでにそのような詳細がある場合は削除してください。"}


def _load(loc):
    return json.loads((ROOT / LOCALES[loc]).read_text())


def _dashfree(text):
    assert "—" not in text and "–" not in text
    assert " - " not in text


@pytest.mark.parametrize("loc", LOCALES)
def test_glance_instruction_carries_the_wire_format_and_the_bracket_ban(loc):
    g = _load(loc)["glanceInstruction"]
    assert "\n<cues>\n" in g and "\n</cues>\n" in g
    assert g.count(" | ") == 2, "the format line is the only pipe line"
    assert NO_BRACKET[loc] in g
    _dashfree(g)


@pytest.mark.parametrize("loc", LOCALES)
def test_reminders_sentinels_are_byte_identical_and_the_block_is_last(loc):
    r = _load(loc)["remindersInstruction"]
    assert "\n---ACTION_ITEMS---\n• " in r
    assert "\n---END_ACTION_ITEMS---\n" in r
    assert r.index("---ACTION_ITEMS---") < r.index("---END_ACTION_ITEMS---")
    _dashfree(r.replace("---ACTION_ITEMS---", "").replace("---END_ACTION_ITEMS---", ""))


@pytest.mark.parametrize("loc", LOCALES)
def test_every_summary_prompt_leaves_out_health_details(loc):
    sp = _load(loc)["summaryPrompts"]
    for kind in ("full", "delta", "consolidation"):
        assert HEALTH[loc] in sp[kind], kind
    assert DELTA_REMOVE[loc] in sp["delta"]
