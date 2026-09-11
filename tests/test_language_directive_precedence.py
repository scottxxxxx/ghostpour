"""The served recipe must DEFER to the language directive, not pre-empt it.

2026-09-11, off Scott's phone: an English phone recording a Spanish meeting got
a Spanish summary, a Spanish title and a Spanish query answer, while the
transcript correctly translated to English. The directive WAS appended (proved
from GP's side: `_output_locale` is assigned only inside
`if _localized_system != body.system_prompt`, so the non-null `output_language`
echo the client received is itself the receipt that injection fired). The model
obeyed the served recipe instead, which said:

    "LANGUAGE: write in the language the participants speak in the transcript,
     whatever language these instructions are written in."

That last clause reads as an explicit instruction to disregard a directive
appended in English, which is exactly what ours is. Two correct halves, one
wrong output, and the deciding sentence sat in the config rather than the code.

The clause lived in SIX places in each of FOUR locale bundles, translated, so
an English-only grep undercounted it by three quarters.

⚠ The query path had NO competing clause and still came back Spanish, so
beating the other sentence is not sufficient on its own: the directive also has
to beat context weight, a 795-character Spanish transcript against one appended
English sentence. That is why the directive now says the material may be
entirely in another language and that this does not change the output language.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import locale_injection

BUNDLES = {
    "config/remote/protected-prompts.json":
        ("write in the language the participants speak in the transcript, "
         "whatever language these instructions are written in",
         "RESPONSE LANGUAGE directive may appear at the end"),
    "config/remote/protected-prompts.es.json":
        ("sea cual sea el idioma de estas instrucciones",
         "puede aparecer una directiva RESPONSE LANGUAGE"),
    "config/remote/protected-prompts.fr.json":
        ("quelle que soit la langue de ces instructions",
         "directive RESPONSE LANGUAGE peut apparaître"),
    "config/remote/protected-prompts.ja.json":
        ("これらの指示が何語で書かれていても",
         "RESPONSE LANGUAGE の指示が現れることがあります"),
}

EXPECTED_OCCURRENCES = 6


def _text(path):
    return json.dumps(json.load(open(path)), ensure_ascii=False)


@pytest.mark.parametrize("path", sorted(BUNDLES))
def test_the_pre_empting_clause_is_gone_from_every_locale(path):
    old, _ = BUNDLES[path]
    assert old not in _text(path), (
        f"{path} still tells the model to ignore the language directive. "
        "This is the sentence that produced a Spanish summary on an English "
        "phone on 2026-09-11.")


@pytest.mark.parametrize("path", sorted(BUNDLES))
def test_every_place_that_had_it_now_defers_to_the_directive(path):
    _, new = BUNDLES[path]
    found = _text(path).count(new)
    assert found == EXPECTED_OCCURRENCES, (
        f"{path}: expected the deferring clause in {EXPECTED_OCCURRENCES} "
        f"places (summaryPrompts full/delta/consolidation, analysisPrompt, "
        f"analyzeSessionPrompt, reanalyzeSummaryPrompt), found {found}. "
        "A partial fix leaves the bug on the surfaces nobody tested.")


# ── the directive itself ──────────────────────────────────────────────────

def test_english_is_directed_not_skipped():
    """The docstring on apply() claimed English was a no-op for a day. It is
    not, and has not been since Scott's 2026-09-10 ruling."""
    assert locale_injection.language_directive("en") is not None
    assert locale_injection.apply("BASE", "en") != "BASE"


def test_a_missing_locale_is_still_a_no_op():
    assert locale_injection.language_directive(None) is None
    assert locale_injection.apply("BASE", None) == "BASE"
    assert locale_injection.apply("BASE", "") == "BASE"


def test_the_directive_claims_precedence_over_the_recipe():
    d = locale_injection.language_directive("en")
    assert "overrides every other language instruction" in d
    assert "transcript or of the participants" in d


def test_the_directive_resists_context_weight():
    """The query path lost to a Spanish transcript with no competing clause,
    so the directive has to say the input's language does not decide."""
    d = locale_injection.language_directive("es")
    assert "entirely in another language" in d


def test_the_directive_lands_after_the_recipe_so_it_reads_last():
    composed = locale_injection.apply("RECIPE TEXT", "es")
    assert composed.index("RECIPE TEXT") < composed.index("RESPONSE LANGUAGE")


def test_the_echo_reports_the_directive_and_nothing_else():
    """`output_language` is what GP DIRECTED, never what was written. SS
    consumed it as observation and stamped a Spanish summary as English."""
    assert locale_injection.output_language_subtag("es-MX") == "es"
    assert locale_injection.output_language_subtag("en") == "en"
    assert locale_injection.output_language_subtag(None) is None
