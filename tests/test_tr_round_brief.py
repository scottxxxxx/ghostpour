"""tr_round_brief: a short prep brief for ONE added interview round (TR contract
docs/tr_round_brief-contract.md in the TR repo, Scott approved 2026-11-09).

The client is promptless and stays dark until GP says the config is live, so
the three things that make it a lane are pinned here: the call type maps to
the config, the config asks for exactly the client's JSON keys, and the call
type has a routing row (no row splits users from the harness, see
test_tr_one_model_per_call_type.py).
"""
import json
import pathlib

from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG, assemble_prompt

CFG_PATH = pathlib.Path("config/remote/techrehearsal/round-brief.json")


def _cfg():
    return json.loads(CFG_PATH.read_text())


def _prompt():
    return " ".join(_cfg()["systemPrompt"].split())


def test_the_call_type_is_a_lane():
    assert _CALL_TYPE_TO_CONFIG["tr_round_brief"] == "techrehearsal/round-brief"
    r = assemble_prompt("tr_round_brief", "THIS ROUND:\nNAME: Bar raiser",
                        {"techrehearsal/round-brief": _cfg()}, scenario_kind="jobInterview")
    assert r and "prep brief for ONE upcoming interview round" in r["system_prompt"]
    assert "{{" not in r["system_prompt"]


def test_it_is_server_only_and_routed_like_every_tr_call():
    assert _cfg()["server_only"] is True
    routing = json.loads(pathlib.Path("config/remote/model-routing.json").read_text())
    models = routing["apps"]["techrehearsal"]["call_types"]["tr_round_brief"]["models"]
    assert len(set(models.values())) == 1


def test_it_asks_for_exactly_the_keys_the_contract_names():
    p = _prompt()
    for key in ('"what_it_tests": [string]', '"likely_questions": [{"question": string',
                '"why": string', '"how_to_win": [string]', '"bring_forward": [string]',
                '"watch_outs": [string]'):
        assert key in p, key
    assert "every key present, arrays allowed to be empty" in p


def test_every_format_pick_the_client_sends_is_named():
    p = _prompt()
    for pick in ("Recruiter screen", "Hiring manager", "Behavioral", "Technical", "System design",
                 "Panel", "Demo or presentation", "Bar raiser", "Culture and fit", "Case"):
        assert f"- {pick}:" in p, pick


def test_both_names_for_the_users_round_text_are_read():
    """The round brief client sends DESCRIBED BY THE USER; the rest of the app
    renamed it WHAT THIS ROUND IS ABOUT on 2026-11-13. Either one carries it."""
    p = _prompt()
    assert "DESCRIBED BY THE USER or WHAT THIS ROUND IS ABOUT" in p


def test_job_prep_is_built_on_not_repeated_and_nothing_is_invented():
    p = _prompt()
    assert "Do not repeat it. Build on it" in p
    assert "Never invent facts about the candidate, the company, the interviewers or the panel" in p
    assert "Never assume anyone's gender" in p


def test_the_dash_rule_names_its_dashes():
    p = _prompt()
    assert "em dashes (U+2014) or en dashes (U+2013)" in p
    assert "—" not in p and "–" not in p
