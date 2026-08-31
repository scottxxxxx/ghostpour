"""Every localised config slug carries every locale, with the same keys.

Scott, 2026-08-23, on finding that protected-prompts.ja did not exist:
"all files that an English user would get, a Japanese language user must
get." The config route resolves `{slug}.{locale}` by Accept-Language and
SILENTLY falls back to the English file when the locale variant is
missing, so a Japanese device had been receiving English prompts with no
error anywhere. The same mechanism hid canned-report.fr (missing) and the
OCR cleanup key (present only in English).

This pins parity structurally: a slug that has any locale variant has all
of them, and every variant carries exactly the English key set. A new
locale or a new key that lands in one file and not the others is a red
test, not a silent fallback.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

LOCALES = ("es", "fr", "ja")
REMOTE = pathlib.Path("config/remote")


def _slugs() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in REMOTE.glob("*.json"):
        m = re.match(r"^(.*?)(?:\.(es|fr|ja))?\.json$", p.name)
        out.setdefault(m.group(1), set()).add(m.group(2) or "en")
    return out


def _paths(o, p=""):
    """Every key path in a JSON document, list indices flattened, so two
    documents with the same shape produce the same set."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _paths(v, f"{p}/{k}")
    elif isinstance(o, list):
        for v in o:
            yield from _paths(v, f"{p}[]")
    else:
        yield p


LOCALISED = sorted(s for s, locs in _slugs().items() if len(locs) > 1)


def test_there_are_localised_slugs_at_all():
    """Guards the parametrised tests below against an empty run."""
    assert len(LOCALISED) >= 6, LOCALISED


@pytest.mark.parametrize("slug", LOCALISED)
def test_a_localised_slug_has_every_locale(slug):
    locs = _slugs()[slug]
    missing = {"en", *LOCALES} - locs
    assert not missing, f"{slug} is missing {sorted(missing)}: those users silently get English"


@pytest.mark.parametrize("slug", LOCALISED)
@pytest.mark.parametrize("loc", LOCALES)
def test_a_locale_variant_carries_exactly_the_english_keys(slug, loc):
    en = json.loads((REMOTE / f"{slug}.json").read_text())
    var_path = REMOTE / f"{slug}.{loc}.json"
    if not var_path.exists():
        pytest.skip("covered by the every-locale test")
    var = json.loads(var_path.read_text())
    en_keys, var_keys = set(_paths(en)), set(_paths(var))
    missing = en_keys - var_keys
    extra = var_keys - en_keys
    assert not missing, f"{slug}.{loc} lacks {sorted(missing)[:6]}: a {loc} user does not get what an English user gets"
    assert not extra, f"{slug}.{loc} has keys English lacks: {sorted(extra)[:6]}"


@pytest.mark.parametrize("loc", LOCALES)
def test_the_prompt_modes_are_translated_not_copied(loc):
    """Parity of KEYS is not parity of content. A locale file that carries
    the English prompt under a Japanese filename satisfies the key test
    and still serves English. The five mode prompts are the most
    user-visible strings in the bundle; none may equal English."""
    en = json.loads((REMOTE / "protected-prompts.json").read_text())["defaultPromptModes"]
    var = json.loads((REMOTE / f"protected-prompts.{loc}.json").read_text())["defaultPromptModes"]
    assert len(var) == len(en)
    for i, (a, b) in enumerate(zip(en, var)):
        assert a["systemPrompt"] != b["systemPrompt"], f"mode {i} in {loc} is the English text"
        assert a["name"] != b["name"], f"mode {i} name in {loc} is the English text"
        # Wire, not prose: these must NOT be translated.
        assert a.get("icon") == b.get("icon")
        assert a.get("requiresContext") == b.get("requiresContext")


@pytest.mark.parametrize("loc", LOCALES)
def test_the_template_placeholders_survive_translation(loc):
    """The client substitutes these by exact name. A translated placeholder
    is a prompt that ships with literal braces in it."""
    en = json.loads((REMOTE / "protected-prompts.json").read_text())["systemPromptTemplate"]
    var = json.loads((REMOTE / f"protected-prompts.{loc}.json").read_text())["systemPromptTemplate"]
    ph = lambda s: sorted(set(re.findall(r"\{\{[^}]+\}\}", s)))
    assert ph(var) == ph(en)


@pytest.mark.parametrize("loc", LOCALES)
def test_the_analysis_schema_keeps_its_wire_vocabulary(loc):
    """Field names and enum values are parsed by code on both sides. The
    prose around them is translated; the tokens are not."""
    var = json.loads((REMOTE / f"protected-prompts.{loc}.json").read_text())["analysisSchema"]
    for token in ('"title"', '"sentimentScore"', '"sentimentLabel"', '"sentimentEmoji"', '"sentimentReason"',
                  '"urgency"', '"urgencyReason"', '"personalityMessage"', '"suggestedTags"', '"tagReasons"',
                  '"enthusiastic"', '"disappointed"', '"low"', '"critical"'):
        assert token in var, f"{loc} analysisSchema lost {token}"


@pytest.mark.parametrize("slug", LOCALISED)
@pytest.mark.parametrize("loc", ("en", *LOCALES))
def test_no_dashes_in_any_locale(slug, loc):
    path = REMOTE / (f"{slug}.json" if loc == "en" else f"{slug}.{loc}.json")
    if not path.exists():
        pytest.skip("covered by the every-locale test")
    text = path.read_text()
    # The OCR cleanup prompt lists garbage glyphs by example and is the one
    # documented exclusion from the no-dash rule.
    d = json.loads(text)
    if isinstance(d, dict):
        d.pop("transcriptCleanup", None)
        text = json.dumps(d, ensure_ascii=False)
    assert "—" not in text and "–" not in text, f"{path.name} carries a dash the model will copy"
