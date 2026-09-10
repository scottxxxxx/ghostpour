"""The STE prompt lint, tested by building the exact sentence each rule names.

WHY THESE TESTS LOOK LIKE THIS. This file lints prompts, so it is an
instrument, and an instrument that has never been sabotaged has been RUN,
not tested. Every rule below therefore gets two assertions: the sentence
built to violate it produces that rule, AND produces no other rule. A test
that only asserts `"STE_PASSIVE" in flags` passes just as happily when the
checker returns every rule for every sentence, which is the failure mode
that would make the whole report meaningless while staying green.

The length rules are checked at the boundary rather than in the middle,
because an off by one in a word count is the defect a 40 word fixture
cannot see.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import prompt_ste_lint as lint


# ── the rules, one crafted sentence each ──────────────────────────────────

def test_clean_sentence_has_no_flags():
    flags, words = lint.check_sentence("Write one short summary of the meeting.")
    assert flags == []
    assert words == 7


def test_long_instruction_fires_only_at_21_words():
    twenty = "Write a summary that keeps every decision and every owner and every date and the open questions in here now"
    assert len(twenty.split()) == 20
    assert lint.check_sentence(twenty)[0] == []

    twenty_one = twenty + " please"
    assert len(twenty_one.split()) == 21
    assert lint.check_sentence(twenty_one)[0] == ["STE_LONG_INSTRUCTION"]


def test_long_descriptive_fires_only_at_26_words():
    twenty_five = ("A meeting summary belongs to the person who ran the meeting and it "
                   "carries the decisions, the owners, the dates and nothing else at all")
    assert len(twenty_five.split()) == 25
    assert lint.check_sentence(twenty_five)[0] == []

    twenty_six = twenty_five + " today"
    assert len(twenty_six.split()) == 26
    assert lint.check_sentence(twenty_six)[0] == ["STE_LONG_DESCRIPTIVE"]


def test_a_sentence_gets_one_length_rule_never_both():
    long_instruction = "Write " + " ".join(["word"] * 40)
    flags = lint.check_sentence(long_instruction)[0]
    assert flags.count("STE_LONG_INSTRUCTION") == 1
    assert "STE_LONG_DESCRIPTIVE" not in flags


def test_multi_instruction_fires_on_a_semicolon_chain():
    sentence = "Keep the owner; never invent a surname; return the summary."
    assert lint.check_sentence(sentence)[0] == ["STE_MULTI_INSTRUCTION"]


def test_multi_instruction_fires_on_a_conjoined_prohibition():
    sentence = "Keep the name as given and never invent surnames."
    assert lint.check_sentence(sentence)[0] == ["STE_MULTI_INSTRUCTION"]


def test_one_instruction_with_a_semicolon_is_not_multi():
    # A semicolon joining an instruction to a fragment that is not itself an
    # instruction is ordinary punctuation, not two instructions.
    sentence = "Keep the owner; the transcript names them."
    assert lint.check_sentence(sentence)[0] == []


def test_passive_fires_and_names_nothing_else():
    assert lint.check_sentence("The summary was written by the model.")[0] == ["STE_PASSIVE"]


def test_passive_exception_list_holds():
    # "is based" is an adjective use, not a passive that hides an actor.
    assert lint.check_sentence("The answer is based on the transcript.")[0] == []


def test_open_pronoun_fires_and_names_nothing_else():
    assert lint.check_sentence("That is the rule.")[0] == ["STE_OPEN_PRONOUN"]


def test_open_pronoun_does_not_fire_mid_sentence():
    assert lint.check_sentence("Keep the rule that the transcript states.")[0] == []


# ── segmentation, the bug that would silently inflate every count ─────────

def test_a_rule_per_line_is_a_sentence_per_line():
    text = "Keep the owner\nNever invent a surname\nReturn the summary"
    assert lint.sentences(text) == [
        "Keep the owner",
        "Never invent a surname",
        "Return the summary",
    ]


def test_bullet_markers_are_stripped_not_counted():
    assert lint.sentences("- Keep the owner\n* Never invent a surname") == [
        "Keep the owner",
        "Never invent a surname",
    ]


def test_sentences_split_on_terminal_punctuation():
    assert lint.sentences("Keep the owner. Never invent a surname.") == [
        "Keep the owner.",
        "Never invent a surname.",
    ]


# ── embedded JSON, the false positive that would get the tool ignored ─────

def test_json_shape_block_is_stripped():
    text = 'Return this shape: {"verdict": string, "sections": [{"topic": string}]} and nothing else.'
    stripped = lint.strip_json_blocks(text)
    assert '"verdict"' not in stripped
    assert "Return this shape:" in stripped
    assert "and nothing else." in stripped


def test_template_variable_survives_stripping():
    text = "Address the applicant as {applicant_name} in every question."
    assert lint.strip_json_blocks(text) == text


# ── the ratios ────────────────────────────────────────────────────────────

def test_prohibition_ratio_counts_both_sides():
    stats = lint.analyse("Never invent a surname\nKeep the name as given\nNever guess a date")
    assert stats["prohibitions"] == 2
    assert stats["affirmations"] == 1
    assert stats["prohibition_ratio"] == 2.0


def test_prohibition_ratio_is_none_with_no_affirmations():
    stats = lint.analyse("Never invent a surname\nNever guess a date")
    assert stats["prohibition_ratio"] is None


def test_emphasis_density_counts_all_caps_tokens():
    stats = lint.analyse("Keep the OWNER and the DATE and the name")
    assert stats["words"] == 9
    assert stats["emphasis_per_1000w"] == 222.2


# ── the baseline gate ─────────────────────────────────────────────────────

def _report(rules, prohibitions=0):
    return {"f.json": {"systemPrompt": {"rules": rules, "prohibitions": prohibitions}}}


def test_baseline_passes_when_a_count_falls(capsys):
    assert lint.compare(_report({"STE_PASSIVE": 2}), _report({"STE_PASSIVE": 5})) == 0


def test_baseline_fails_when_a_count_rises(capsys):
    assert lint.compare(_report({"STE_PASSIVE": 6}), _report({"STE_PASSIVE": 5})) == 1
    assert "STE_PASSIVE 5 -> 6" in capsys.readouterr().out


def test_baseline_fails_when_prohibitions_rise():
    assert lint.compare(_report({}, prohibitions=9), _report({}, prohibitions=8)) == 1


def test_baseline_ignores_a_prompt_the_baseline_never_saw():
    assert lint.compare(_report({"STE_PASSIVE": 99}), {}) == 0


# ── the real corpus, so a config edit that breaks parsing is visible ──────

def test_served_prompts_parse_and_produce_a_report():
    result = lint.run(lint.DEFAULT_ROOTS)
    assert "config/remote/protected-prompts.json" in result
    summary = lint.summarise(result)
    assert summary["sentences"] > 500
    assert summary["words"] > 10000
