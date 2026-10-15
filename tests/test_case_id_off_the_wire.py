"""The case id never reaches the model provider (Scott, 2026-10-14).

Until N-400 v61 and I-765 v4 both user templates carried "CASE ID:
{{case_id}}", so every turn of one application sent the provider a stable id
for that application. GP still keeps case_id on its own side (usage_log, the
per-application allowance); it just must not be in the prompt.

Checked through the real assembler with the whole metadata bag the route
hands it, case_id included, so a placeholder added back anywhere in the
template goes red here.
"""
import json
from pathlib import Path

import pytest

from app.services.prompt_assembly import assemble_prompt

SENTINEL = "c4se1d9f"
LANES = [
    ("n400_interviewer_turn", "n400/interviewer-turn", "config/remote/n400/interviewer-turn.json"),
    ("i765_interviewer_turn", "i765/interviewer-turn", "config/remote/i765/interviewer-turn.json"),
]


@pytest.mark.parametrize("call_type,slug,path", LANES)
def test_the_assembled_prompt_never_carries_the_case_id(call_type, slug, path):
    cfg = json.loads(Path(path).read_text())
    variables = {
        "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
        "turn_id": "t_001", "conversation": "[start of interview]",
        "known_facts": "nothing yet", "agenda": "q | Part 1: x | f | Q?",
        "case_id": SENTINEL,
    }
    out = assemble_prompt(call_type, "[start of interview]", {slug: cfg}, variables=variables)
    assert out is not None
    assert "TURN ID: t_001" in out["user_content"], "the assembler did not run this template"
    assert SENTINEL not in out["user_content"]
    assert SENTINEL not in out["system_prompt"]
    assert "CASE ID" not in out["user_content"]


@pytest.mark.parametrize("call_type,slug,path", LANES)
def test_case_id_is_not_a_declared_prompt_variable(call_type, slug, path):
    cfg = json.loads(Path(path).read_text())
    assert "case_id" not in cfg["optionalVariables"]
    assert "case_id" not in (cfg.get("requiredVariables") or [])
