"""A deferral disclosure must not be conditional on whether she comes back.

2026-09-11, Scott's ruling: "if needed is wrong, she needs to know she must
come back."

The DEFERRALS block already said EVERY DEFERRED FIELD, NAMED. That rule is
about COVERAGE and it is fully satisfied by "I've noted June 2020 to firm up
later if needed": right field, correctly attached, same breath. What the block
never said is that the clause has to be UNCONDITIONAL, and that is a separate
failure with its own two-word signature.

Measured by the auditor on 30 hand-labelled post-fix replies: 3 do this, and
ONE OF THE THREE CAME FROM THE PROMPT SERVING AT THE TIME. Both instruments
scored all three as disclosure, because both look for a hedge attached to the
right field and neither has any notion of a conditional. So this was invisible
to every check either team had.

⚠ THE SECOND TEST IS THE ONE THAT MATTERS. The obvious fix is "forbid
conditionals", which would strip "if you can find it" and "when you have your
mail in front of you", phrasings that are genuinely kind to someone who does
not have her paperwork to hand. That version scores better on any readability
lint and makes the product worse, which is exactly the trade the four-field
attachment failure already demonstrated. A condition on HOW she gets the answer
keeps the return certain; only a condition on WHETHER cancels it.
"""

import json

import pytest

CONFIG = "config/remote/n400/interviewer-turn.json"
DEFERRALS_LINE = 83


@pytest.fixture(scope="module")
def prompt():
    return json.load(open(CONFIG))["systemPrompt"]


@pytest.fixture(scope="module")
def deferrals(prompt):
    return prompt.split("\n")[DEFERRALS_LINE]


def test_the_rule_is_in_the_deferrals_block_not_merely_in_the_file(deferrals):
    """A rule in the wrong section is a rule the model reads under the wrong
    heading. The block is line 83; the file is 138 lines."""
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in deferrals


def test_the_rule_appears_exactly_once(prompt):
    assert prompt.count("AND THE CLAUSE MUST NOT BE CONDITIONAL") == 1


@pytest.mark.parametrize("failure", [
    "to firm up later IF NEEDED",
    "we can pin the exact day later IF IT MATTERS",
    "to verify the exact days IF NEEDED",
])
def test_all_three_measured_failures_are_quoted_verbatim(deferrals, failure):
    """Quoted, not paraphrased, the same way the three ZIP turns are. A rule
    stated in the abstract is the one the model satisfies in the letter."""
    assert failure in deferrals


def test_the_positive_replacement_is_given(deferrals):
    """STE and this project's own history: a prohibition without a positive
    form leaves the model to pick a scope."""
    assert "Say the return as a fact, not a possibility" in deferrals
    assert "I'll need the exact day before this is finished" in deferrals


# ── the over-correction guard ─────────────────────────────────────────────

@pytest.mark.parametrize("kind_phrasing", [
    "if you can find it",
    "when you have your mail in front of you",
])
def test_a_condition_on_MEANS_is_explicitly_still_allowed(deferrals, kind_phrasing):
    """Without this the rule reads as "forbid conditionals", which strips a
    phrasing that is kind to someone without her paperwork and scores BETTER
    on every readability metric while making the copy worse."""
    assert kind_phrasing in deferrals


def test_the_distinction_is_stated_as_a_rule_not_left_to_inference(deferrals):
    assert "A condition on HOW she gets the answer" in deferrals
    assert "A condition on WHETHER she has to come back is not" in deferrals


# ── the rule it refines must survive ──────────────────────────────────────

def test_the_existing_good_examples_sentence_is_intact(deferrals):
    """The new text was inserted after this sentence because it refines it.
    An insertion that ate its own anchor would remove the lane's only worked
    examples of doing this well."""
    for example in ("por verificar", "I'll leave that one open",
                    "take your time and let me know when you find it"):
        assert example in deferrals


def test_the_coverage_rule_is_untouched(deferrals):
    """Coverage and unconditionality are two rules. Adding the second must not
    cost the first."""
    assert "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM" in deferrals


def test_version_moved_past_the_v29_that_shipped_the_defect(prompt):
    assert json.load(open(CONFIG))["version"] >= 30
