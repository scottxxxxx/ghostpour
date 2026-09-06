"""The flag said not over. The sentence told her to sign and file.

conf-es-full-1, the first Spanish run ever taken past Part 2. She said
"sí, sí a todo" to the oath; ONE field of six reached her form. Then:

  turn 86  "Con esto queda completa toda la solicitud."
  turn 87  "Usted misma la firma y la presenta."

`interview_over` was never true in 99 turns and
`clear_interview_over_while_agenda_open` fired correctly at 86, 87 and 99
with the oath node still on the agenda. The machine-readable field was
right the whole time. Only the sentence speaks to her, and it told her to
file an application missing five required answers.

Narrow on purpose: fires only where the agenda still holds an open node,
which is the one place a closing sentence is provably wrong rather than
merely early.
"""
import json

import pytest

from app.services.n400_interviewer_guard import (
    closes_in_words_while_agenda_open, mark_closing_while_open,
)

AGENDA = ("q_p9_oath | Part 9: El juramento | "
          "p9.willing_bear_arms,p9.understand_oath,p9.willing_full_oath | "
          "¿Está dispuesta a prestar el juramento completo?")


def _reply(text, locale="es"):
    return json.dumps({"intent": "answer", "facts": [], "reply": {locale: text}})


REAL_AND_CONSTRUCTED = [
    # the two sentences she actually heard
    ("turn_86_verbatim", "Con esto queda completa toda la solicitud.", "es", True),
    ("turn_87_verbatim", "Usted misma la firma y la presenta.", "es", True),
    # a PART claim is not an APPLICATION claim and must never match
    ("part_claim_is_fine", "Con esto queda completa la Parte 9, ¿es correcto?", "es", False),
    ("en_part_claim_is_fine", "That completes Part 9, is that right?", "en", False),
    ("en_sign_and_file", "Your application is complete, you can sign and file it.", "en", True),
    ("en_we_are_done", "We're done, that's everything.", "en", True),
    ("pt_close", "Você já pode assinar e enviar.", "pt", True),
    ("ordinary_question", "¿Cuál es su fecha de nacimiento?", "es", False),
    ("ordinary_en", "What is your date of birth?", "en", False),
]


@pytest.mark.parametrize("label,sentence,locale,must_flag", REAL_AND_CONSTRUCTED,
                         ids=[c[0] for c in REAL_AND_CONSTRUCTED])
def test_closing_sentences(label, sentence, locale, must_flag):
    got = closes_in_words_while_agenda_open(_reply(sentence, locale), AGENDA)
    assert (got is not None) == must_flag, sentence


def test_an_empty_agenda_means_the_interview_really_IS_over():
    """The guard must not block a legitimate close. When nothing is open,
    telling her she is finished is correct and required."""
    for agenda in ("", None):
        assert closes_in_words_while_agenda_open(
            _reply("Con esto queda completa toda la solicitud."), agenda) is None


def test_the_part_claim_boundary_is_the_whole_discriminator():
    """The lane says 'that completes Part N' at every section checkpoint,
    dozens of times per interview. If those flagged, the guard would fire
    constantly and be turned off."""
    for s in ("Con esto queda completa la Parte 5.",
              "That completes Part 12, is that right?",
              "Queda completa la Parte 9, ¿es correcto?"):
        assert closes_in_words_while_agenda_open(_reply(s), AGENDA) is None, s


def test_the_marker_carries_the_phrase_and_the_open_nodes():
    info = closes_in_words_while_agenda_open(
        _reply("Con esto queda completa toda la solicitud."), AGENDA)
    m = json.loads(mark_closing_while_open(
        _reply("otra cosa"), info, retried=True, resolved=False))["closed_in_words_while_open"]
    assert m["locale"] == "es" and m["retried"] is True and m["resolved"] is False
    assert m["open_nodes"] == ["q_p9_oath"]
    assert "solicitud" in m["phrase"]


def test_malformed_input_is_not_a_flag():
    for t in ("not json", "[1,2]", json.dumps({"reply": None})):
        assert closes_in_words_while_agenda_open(t, AGENDA) is None
