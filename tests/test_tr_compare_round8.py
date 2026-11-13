"""TR round 8 asks on tr_compare_reality, and the no-dash rule in every TR prompt
(TR inbox 2026-11-09 and 2026-11-12, Scott 2026-11-13: "whatever they asked, do it").

The client (TR CompareRealityClient.swift) decodes `question`, `practice_index`,
`rating`, `held` and `fix` on each section and reads `mixed` and `new` as delta
values; the served prompt never asked for any of them, so they never came back.
"""
import glob
import json
import pathlib

CONFIG = pathlib.Path("config/remote/techrehearsal/compare-reality.json")


def _prompt() -> str:
    return " ".join(json.loads(CONFIG.read_text())["systemPrompt"].split())


def test_every_field_the_client_decodes_is_asked_for():
    p = _prompt()
    for field in ('"held": string', '"fix": string', '"practice_index": integer or null',
                  '"question": string or null', '"rating": string or null'):
        assert field in p, field
    assert '"Weak" | "Meets" | "Strong" | "Bar Raiser"' in p, "the scorer's own scale"


def test_mixed_takes_a_topic_that_held_in_part_and_drifted_only_when_nothing_did():
    p = _prompt()
    assert '"mixed" = the topic came up and PART of what was rehearsed held' in p
    assert '"drifted" is only for a topic where nothing rehearsed came through' in p
    assert "never praise a topic in its note and mark it \"drifted\"" in p


def test_practice_is_graded_from_the_users_own_replies():
    p = _prompt()
    assert "PRACTICE block" in p and "numbered from 0" in p
    assert "YOU and ANSWER are the user's own practice words" in p
    assert "never claim the user said something in practice that no YOU or ANSWER line shows" in p


def test_the_counterpart_line_has_no_doubled_article():
    assert "The counterpart is: {{counterpart}}." in _prompt()


def _tr_prompt_files():
    return sorted(glob.glob("config/remote/techrehearsal/*.json"))


def test_no_tr_prompt_serves_the_mangled_dash_rule():
    """Served as "em dashes (, ) or en dashes (-)": the model never saw which
    characters to avoid. Named by code point now, so a dash-stripping pass
    cannot eat them again."""
    bad = [p for p in _tr_prompt_files() if "(, ) or en dashes (-)" in pathlib.Path(p).read_text()]
    assert not bad, bad
    named = [p for p in _tr_prompt_files() if "em dashes (U+2014) or en dashes (U+2013)" in pathlib.Path(p).read_text()]
    assert len(named) >= 10, named


def test_no_tr_prompt_doubles_the_counterpart_article():
    bad = [p for p in _tr_prompt_files() if "is the {{counterpart}}" in pathlib.Path(p).read_text()]
    assert not bad, bad
