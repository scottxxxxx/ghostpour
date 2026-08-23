"""Help Me Respond must not invent the user (2026-08-23).

Scott caught it live: a user ran a two-minute transcript containing ONE
line, an interviewer asking about their development areas, through Help
Me Respond, and got back a confident first-person biography. Delegation,
prioritization, shorter internal deadlines. Not one word of it was in the
input. Every fact was about a person the model had never seen a single
word from, written for them to say out loud.

The eval that followed (8 transcripts x 5 modes, through the real served
prompts) found the defect is isolated: the other four modes were honest
on every thin input, and Help Me Respond fabricated in exactly the three
cases where the transcript put a question TO the user with no facts FROM
the user. The shared rules are fine. The mode prompt was the trap: it
asked for "something the user would actually say", rule 3 said any
fragment is enough, and rule 4 only permitted declining on silence or
small talk. Given those three, there was no honest output available.

These pin the SHAPE of the served prompt, which is the only part a unit
test can see. The behaviour is measured by the eval harness in
scratchpad/promptEval and recorded in the PR; a test that asserted the
model's output would be testing the model.
"""
from __future__ import annotations

import json

import pytest

LOCALES = ["", ".es", ".fr"]


def _mode(suffix: str) -> dict:
    d = json.load(open(f"config/remote/protected-prompts{suffix}.json"))
    m = d["defaultPromptModes"][1]
    return m


@pytest.mark.parametrize("suffix", LOCALES)
def test_mode_one_is_still_help_me_respond(suffix):
    """Index is load bearing: the client addresses modes by position."""
    name = _mode(suffix)["name"].lower()
    assert "respond" in name or "responder" in name or "répondre" in name, name


@pytest.mark.parametrize("suffix,never,brackets", [
    ("", "never invent facts about the user", "square brackets"),
    (".es", "nunca inventes hechos sobre el usuario", "entre corchetes"),
    (".fr", "n'invente jamais de faits sur l'utilisateur", "entre crochets"),
])
def test_the_mode_forbids_inventing_the_user_and_says_what_to_do_instead(suffix, never, brackets):
    """Both halves, because the first alone produces hedging and rule 4
    forbids hedging: the model needs a sanctioned output shape (a
    bracketed scaffold) or the two instructions fight and rule 4 wins,
    which is how the fabrication happened in the first place."""
    text = _mode(suffix)["systemPrompt"].lower()
    assert never in text
    assert brackets in text


@pytest.mark.parametrize("suffix", LOCALES)
def test_the_scaffold_is_declared_a_complete_answer_not_hedging(suffix):
    """The sentence that stops rule 4 from overriding the new rule."""
    text = _mode(suffix)["systemPrompt"].lower()
    assert any(k in text for k in ("not hedging", "no es una evasiva", "n'est pas une dérobade"))


@pytest.mark.parametrize("suffix", LOCALES)
def test_the_framing_rule_is_present(suffix):
    """The residue the first draft left: it bracketed the detail and kept
    an invented premise ("it's a bit of both"). Three of three re-runs
    were clean once the framing itself was declared the user's to fill."""
    text = _mode(suffix)["systemPrompt"].lower()
    assert any(k in text for k in ("that choice is theirs", "esa elección es suya", "ce choix lui appartient"))


@pytest.mark.parametrize("suffix", LOCALES)
def test_the_full_draft_path_survives(suffix):
    """The fix must not turn every reply into a scaffold. Five of the
    eight eval transcripts had the user's stance in them and all five
    still produced full drafts with zero brackets."""
    text = _mode(suffix)["systemPrompt"].lower()
    assert any(k in text for k in ("draft the full reply", "redacta la respuesta completa", "rédige la réponse complète"))


@pytest.mark.parametrize("suffix", LOCALES)
def test_no_dashes_in_the_served_mode(suffix):
    text = _mode(suffix)["systemPrompt"]
    assert "—" not in text and "–" not in text


def test_the_other_four_modes_are_untouched():
    """The eval showed they were honest. Changing them would be changing
    something that was not broken, in a PR whose evidence is about mode 1."""
    import subprocess
    before = json.loads(subprocess.run(
        ["git", "show", "main:config/remote/protected-prompts.json"],
        capture_output=True, text=True, check=True).stdout)["defaultPromptModes"]
    after = json.load(open("config/remote/protected-prompts.json"))["defaultPromptModes"]
    for i in (0, 2, 3, 4):
        assert before[i]["systemPrompt"] == after[i]["systemPrompt"], f"mode {i} changed"


def test_the_shared_rules_are_untouched():
    """Scott asked whether the system prompt needs to change. The honest
    answer from the eval is no, and this pins that the answer was acted on."""
    import subprocess
    before = json.loads(subprocess.run(
        ["git", "show", "main:config/remote/protected-prompts.json"],
        capture_output=True, text=True, check=True).stdout)
    after = json.load(open("config/remote/protected-prompts.json"))
    assert before["defaultUserInstructions"] == after["defaultUserInstructions"]
    assert before["systemPromptTemplate"] == after["systemPromptTemplate"]
