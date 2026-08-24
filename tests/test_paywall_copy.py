"""Served paywall copy (Scott 2026-08-23): every string on the upgrade
screen comes from tiers{.locale}.paywall so copy changes never need an
App Store release. SS renders with its bundled strings as offline floor."""
import json

import pytest

LOCALES = ["", ".es", ".fr", ".ja"]


def _paywall(suffix):
    with open(f"config/remote/tiers{suffix}.json", encoding="utf-8") as f:
        return json.load(f)["paywall"]


def _leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v)
    else:
        yield node


@pytest.mark.parametrize("suffix", LOCALES)
def test_no_empty_strings_and_no_dashes(suffix):
    for leaf in _leaves(_paywall(suffix)):
        if isinstance(leaf, str):
            assert leaf.strip(), suffix
            assert "—" not in leaf and "–" not in leaf, (suffix, leaf)


@pytest.mark.parametrize("suffix", LOCALES)
def test_same_shape_as_english(suffix):
    def shape(n):
        if isinstance(n, dict):
            return {k: shape(v) for k, v in n.items()}
        if isinstance(n, list):
            return [shape(v) for v in n]
        return type(n).__name__
    assert shape(_paywall(suffix)) == shape(_paywall(""))


@pytest.mark.parametrize("suffix", LOCALES)
def test_plural_templates_carry_n_and_badge_is_not_pro_plus(suffix):
    pw = _paywall(suffix)
    for unit in ("day", "week", "month", "year"):
        for form in ("one", "other"):
            assert "{n}" in pw["trial_pill"][unit][form], (suffix, unit, form)
            assert "{n}" in pw["welcome_back"][unit][form], (suffix, unit, form)
    # The Pro+ badge misled: Plus carries CQ (30-day window). Served copy
    # must never resurrect it.
    for leaf in _leaves(pw):
        if isinstance(leaf, str):
            assert leaf != "Pro+", suffix


def test_es_week_pluralization_is_the_bug_fix():
    """'Prueba de 1 semanas' is the client bug this block exists to kill."""
    pw = _paywall(".es")
    assert pw["trial_pill"]["week"]["one"].endswith("semana")
    assert pw["trial_pill"]["week"]["other"].endswith("semanas")
