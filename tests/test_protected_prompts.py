"""Schema + content tests for config/remote/protected-prompts*.json.

The defaultUserInstructions string is mission-critical wire-shape: it
becomes part of the system prompt for every chat call. A regression
that silently drops a rule has caused user-visible failures before
(see 2026-05-20 image-attachment failure, where the
'What Are We Missing?' quick prompt hijacked the response and the
model hallucinated a watermark because no rule told it to acknowledge
attached images).

This file locks the rules that exist for known-incident reasons. Add
to it whenever a rule's removal would re-open a postmortem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

LOCALE_FILES = [
    "config/remote/protected-prompts.json",
    "config/remote/protected-prompts.es.json",
    "config/remote/protected-prompts.fr.json",
    "config/remote/techrehearsal/protected-prompts.json",
]


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


@pytest.mark.parametrize("path", LOCALE_FILES)
def test_default_user_instructions_present(path):
    """Every locale variant must carry the system instructions."""
    data = _load(path)
    assert "defaultUserInstructions" in data
    assert len(data["defaultUserInstructions"]) > 0


@pytest.mark.parametrize("path", LOCALE_FILES)
def test_image_acknowledgement_rule_present(path):
    """A rule must instruct the model to acknowledge attached image
    content before applying other framing. This is the defensive fix
    for the 2026-05-20 'What Are We Missing?' prompt-mode collision:
    when iOS sent the wrong prompt_mode along with an image, the
    blind-spots template hijacked the response and the model
    hallucinated a watermark.

    The rule's presence is the contract. The exact wording can be
    refined; what we lock in is that *some* rule mentions images +
    describe/acknowledge + before/prioritize-the-user."""
    instructions = _load(path)["defaultUserInstructions"].lower()
    # English contract token. The Spanish file also includes this
    # mirrored phrasing via an explicit Spanish rule string; we
    # check for the per-locale token below.
    if "es.json" in path:
        # Spanish rule references "imagen" + "describe" + "marco" (framing).
        assert "imagen" in instructions
        assert "describe" in instructions
        # "Antes de aplicar cualquier otro marco" — the key directive.
        assert "antes de aplicar" in instructions
    elif "fr.json" in path:
        # French rule: image + describe + before-applying directive.
        assert "image est jointe" in instructions
        assert "décris" in instructions
        assert "avant d'appliquer" in instructions
    else:
        # English content (en and tr-prefixed locales share English).
        assert "image is attached" in instructions
        assert "before applying" in instructions
        # The literal-question override is the other half of the rule.
        assert "literal question" in instructions


SS_LOCALE_FILES = [p for p in LOCALE_FILES if "techrehearsal/" not in p]


@pytest.mark.parametrize("path", SS_LOCALE_FILES)
def test_build_804_prompt_keys_present(path):
    """SS build 804 moved two compiled prompts to config fields read via
    decodeIfPresent: reanalyzeSummaryPrompt (summary regen after
    speaker-name corrections) and followUpInstruction (replaces the mode
    prompt on follow-up turns). Seeded 2026-07-27 from SS's compiled
    fallback; served in English for every locale per SS (their prompt
    text has never been localized). Dropping a key silently reverts
    those clients to the bundled fallback, so presence is the contract.
    followUpInstruction must read as a standalone instruction: the
    client may append an output-format block after it."""
    data = _load(path)
    for key in ("reanalyzeSummaryPrompt", "followUpInstruction"):
        assert key in data, f"{path} missing {key}"
        assert len(data[key]) > 0
        assert "—" not in data[key] and "–" not in data[key]


@pytest.mark.parametrize("path", LOCALE_FILES)
def test_version_bumped_after_rule_addition(path):
    """The rule landed at v8 (en + es) and v2 (tr). If anyone reverts
    or rolls back the file, this test catches it."""
    data = _load(path)
    minimum = 2 if "techrehearsal/" in path else 8
    assert data["version"] >= minimum, (
        f"{path} version={data['version']} — image-acknowledgement rule "
        f"shipped at v{minimum}; lower version means the rule may be missing"
    )


@pytest.mark.parametrize("path", SS_LOCALE_FILES)
def test_summarizers_carry_name_grounding(path):
    """2026-07-28: the summarizer titled a vet meeting 'Max Verstappen
    Veterinary Care' — the dog is named Max, no surname anywhere in the
    transcript, and the prompt never forbade inventing one. Every
    summarizer prompt now grounds names/facts to the transcript. The
    reanalyze key is English in all locales (SS contract); summaryPrompts
    carry the clause in their own language."""
    data = _load(path)
    assert "never invent" in data["reanalyzeSummaryPrompt"]
    token = {"es.json": "nunca inventes", "fr.json": "n'invente jamais"}.get(
        path.split("protected-prompts.")[-1], "never invent")
    for k in ("full", "delta", "consolidation"):
        assert token in data["summaryPrompts"][k], (path, k)


@pytest.mark.parametrize("path", SS_LOCALE_FILES)
def test_served_prompts_carry_no_literal_dashes(path):
    """2026-08-12 field test: a Project Chat answer shipped em dashes.
    Models copy the punctuation they see, so the served prompt strings
    themselves must never carry an em or en dash (Scott's standing
    rule; the build-804 per-key checks, generalized to every string in
    the file)."""
    def walk(v, where):
        if isinstance(v, str):
            assert "—" not in v and "–" not in v, where
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{where}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{where}[{i}]")
    walk(_load(path), path)


_DASH_BAN_TOKENS = {
    "protected-prompts.json": (
        "Never use em dashes", "spaced hyphen",
        "never copy that punctuation"),
    "protected-prompts.es.json": (
        "Nunca uses rayas", "guion suelto",
        "nunca copies esa puntuación"),
    "protected-prompts.fr.json": (
        "tiret cadratin", "trait d'union isolé",
        "ne copie jamais cette ponctuation"),
}


@pytest.mark.parametrize("path", SS_LOCALE_FILES)
def test_chat_template_dash_ban_strengthened(path):
    """The chat lane's template ban was present but lost to dash-heavy
    injected context (2026-08-12: live output carried em dashes). The
    strengthened ban names the contaminated context, forbids copying
    its punctuation, and closes the spaced-hyphen dodge. Presence of
    all three clauses is the contract."""
    tokens = _DASH_BAN_TOKENS[path.split("/")[-1]]
    tpl = _load(path)["systemPromptTemplate"]
    for token in tokens:
        assert token in tpl, (path, token)
