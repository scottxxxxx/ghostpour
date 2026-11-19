import json
from pathlib import Path

from app.services.chat_citations import append_citation_rule, citation_rule

CFG = {"protected-prompts": json.loads(
    Path("config/remote/protected-prompts.json").read_text())}
RULE = CFG["protected-prompts"]["chatCitationInstruction"]
CTX = '## Meeting 1 of 2 · "Ozmo" [ref m6D8A]\n[12:34] Dana: ship in March.'


def test_served_rule_has_the_citation_shape_and_no_dashes():
    assert "[m6D8A 12:34]" in RULE
    assert "—" not in RULE and "–" not in RULE


def test_appended_on_project_chat_with_refs():
    out = append_citation_rule("SYS", CTX, "ProjectChat", CFG)
    assert out == "SYS\n\n" + RULE


def test_appended_on_meeting_chat_when_ref_in_system():
    out = append_citation_rule("SYS " + CTX, "q", "PostMeetingChat", CFG)
    assert out.endswith(RULE)


def test_not_appended_without_refs():
    assert append_citation_rule("SYS", "[12:34] Dana: hi", "ProjectChat", CFG) == "SYS"


def test_not_appended_on_other_modes():
    assert append_citation_rule("SYS", CTX, "Catch Me Up", CFG) == "SYS"


def test_not_appended_twice():
    once = append_citation_rule("SYS", CTX, "ProjectChat", CFG)
    assert append_citation_rule(once, CTX, "ProjectChat", CFG) == once


def test_missing_key_is_a_no_op():
    assert citation_rule({}) is None
    assert append_citation_rule("SYS", CTX, "ProjectChat", {}) == "SYS"
