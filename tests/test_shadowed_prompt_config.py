"""A client prompt silently shadows every served value (2026-08-08).

TR's handover process note, and the defect behind two incidents on one
mode. Assembly runs only for promptless calls, so when a client sends its
own system_prompt every served value for that call type is ignored:
maxTokens, temperature, thinking, the prompt itself. Nothing errors and
nothing logs.

LiveRoundScore truncated a real 44-minute interview at 4096 tokens twice
while we held modes.LiveRoundScore.maxTokens: 16384. Both times the fix
was deployed, verified live in the served config, and reached nothing.
The config looked correct in the dashboard and was decoration on the wire.

A warning, not a rejection. Bootstrap prompting is the legitimate carrying
state during a migration, and refusing those calls would break the client
mid-flip, which is the opposite of the point. What it must never be again
is invisible.
"""

import json
import pathlib

from app.services.prompt_assembly import shadowed_config_keys

CONFIGS = {
    "techrehearsal/response-analysis": json.loads(
        pathlib.Path("config/remote/techrehearsal/response-analysis.json").read_text()),
    "techrehearsal/mock-interview": json.loads(
        pathlib.Path("config/remote/techrehearsal/mock-interview.json").read_text()),
}


def test_it_names_what_a_client_prompt_would_have_discarded():
    """The case that cost two incidents: a mode whose budget we hold."""
    shadowed = shadowed_config_keys(
        "tr_response_analysis", CONFIGS, prompt_mode="LiveRoundScore")
    assert "maxTokens" in shadowed
    assert "systemPrompt" in shadowed


def test_a_mode_budget_counts_even_when_the_file_has_no_such_key():
    """The exact shape of the LiveRoundScore miss: the value lived on the
    mode, not the file, so anything checking the file alone saw nothing."""
    doc = json.loads(json.dumps(CONFIGS["techrehearsal/response-analysis"]))
    doc["modes"]["LiveRoundScore"] = {"maxTokens": 16384}
    doc.pop("maxTokens", None)
    shadowed = shadowed_config_keys(
        "tr_response_analysis", {"techrehearsal/response-analysis": doc},
        prompt_mode="LiveRoundScore")
    assert "maxTokens" in shadowed


def test_an_unregistered_call_type_says_nothing():
    """The ordinary case for a client prompt. A warning here would fire on
    every call we hold no opinion about, and a warning that fires always is
    a warning nobody reads."""
    assert shadowed_config_keys("tr_query", CONFIGS) == set()
    assert shadowed_config_keys("not_a_call_type", CONFIGS) == set()


def test_a_missing_config_says_nothing():
    """Registered in the map but absent from what is served, e.g. mid
    migration. Nothing is being shadowed because nothing is there."""
    assert shadowed_config_keys("tr_response_analysis", {}) == set()
    assert shadowed_config_keys("tr_response_analysis", None) == set()


def test_structure_keys_are_not_reported():
    """`modes`, `version` and `server_only` are structure rather than
    values. Naming them would make the warning noisy enough to ignore,
    which is the failure mode this whole thing exists to avoid."""
    shadowed = shadowed_config_keys(
        "tr_response_analysis", CONFIGS, prompt_mode="LiveRoundScore")
    for noise in ("modes", "version", "server_only", "recommendedModel"):
        assert noise not in shadowed


def test_an_empty_served_value_is_not_reported():
    """mock-interview ships an empty userPromptTemplate. Reporting a key
    whose value is empty would claim the client discarded something it did
    not."""
    doc = {"version": 1, "systemPrompt": "x", "userPromptTemplate": "",
           "maxTokens": None}
    shadowed = shadowed_config_keys(
        "tr_mock_interview", {"techrehearsal/mock-interview": doc})
    assert shadowed == {"systemPrompt"}


def test_the_guard_runs_before_assembly_and_only_warns():
    """Source-level, because the ordering is the contract: the check has to
    sit on the client-prompt branch, and it must not reject."""
    src = pathlib.Path("app/routers/chat.py").read_text()
    guard = src.index("prompt_config_shadowed")
    assembly = src.index("# 2.5. Server-side prompt assembly")
    assert guard < assembly, "the guard belongs on the client-prompt branch"
    block = src[src.index("if body.system_prompt:"):assembly]
    assert "logger.warning" in block
    assert "raise HTTPException" not in block, (
        "bootstrap prompting is a legitimate carrying state during a "
        "migration; rejecting it would break the client mid-flip")
