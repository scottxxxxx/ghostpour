"""Rule 7 of the shared SS rules: answer the last question asked (2026-10-22).

Scott, live in an interview: the interviewer followed "have you hired people"
with "did you build the interview loops yourself and make the final call?".
The second answer opened with one invented sentence about a "collaborative
review" and then retold the first answer, which Scott had just read aloud and
which sat in the transcript right above the new question. The custom prompt
already said "answer only that question"; its fixed format (an example, then
a tie back) pulled the model into restating the hiring story anyway.

Rule 7 lives in GP's shared rules, ahead of any user or mode prompt, so it
holds for every SS user. Subagent replay of the two real requests (no API
spend): the follow-up went from about 120 words of retelling to two or three
sentences on the new question, with no invented decision process; the fresh
question still got its full answer. These pin that the served text says it,
in every locale; whether the model obeys is what the replay measured.
"""
from __future__ import annotations

import json

import pytest

RULE7 = {
    "": ("the last one asked", "do not retell the earlier answer", "Never invent a fact"),
    ".es": ("la última que se hizo", "no vuelvas a contar la respuesta anterior", "Nunca inventes un dato"),
    ".fr": ("la dernière posée", "ne raconte pas de nouveau la réponse précédente", "N'invente jamais un fait"),
    ".ja": ("最後にされた質問", "前の答えやその例を繰り返さない", "事実を作り出してはいけません"),
}


def _rules(suffix: str) -> str:
    return json.load(open(f"config/remote/protected-prompts{suffix}.json"))["defaultUserInstructions"]


@pytest.mark.parametrize("suffix", list(RULE7))
def test_rule_seven_is_served_in_every_locale(suffix):
    text = _rules(suffix)
    for phrase in RULE7[suffix]:
        assert phrase in text, (suffix, phrase)


@pytest.mark.parametrize("suffix", list(RULE7))
def test_rule_seven_comes_after_rule_six(suffix):
    text = _rules(suffix)
    assert text.index("6.") < text.rindex("\n7. ")


@pytest.mark.parametrize("suffix", list(RULE7))
def test_rule_seven_has_no_dash_punctuation(suffix):
    rule = _rules(suffix)[_rules(suffix).rindex("\n7. "):]
    assert "—" not in rule and "–" not in rule and " - " not in rule
