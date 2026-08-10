"""The last four compiled prompts on a live GP path (2026-08-10).

Items 2 to 5 of TR's handover. Scott's ruling, restated because it is the
reason all of this moved: no prompt is the client's to own. The client
provides the text once, GP serves and controls it from then on, and a
wording fix is a middleware change rather than an App Store review cycle.

Absorbed verbatim, so each flip is behaviour-preserving and the client can
delete its copy in a promptless commit without explaining a change to
anyone.
"""

import json
import pathlib

import pytest

LOCALES = ("", ".es", ".fr")


def _pp(loc=""):
    return json.loads(
        pathlib.Path(f"config/remote/protected-prompts{loc}.json").read_text())


def _mock():
    return json.loads(
        pathlib.Path("config/remote/techrehearsal/mock-interview.json").read_text())


# --- item 2: InterviewHint --------------------------------------------


def test_the_hint_is_the_text_the_user_actually_gets_today():
    """We already held a modes.InterviewHint prompt and it was never used,
    because the client sends its own. Ours was tighter (90 words, one
    paragraph); theirs is what ships. Absorbing OURS would have changed
    behaviour at the flip, which is the one thing absorption must not do.
    Improvements come after, by config, where they are visible."""
    sp = _mock()["modes"]["InterviewHint"]["systemPrompt"]
    assert "Write 2 short paragraphs" in sp
    assert "90 words" not in sp


def test_the_hint_stays_a_nudge_and_not_the_answer():
    """The whole reason it is a separate mode from InterviewModelAnswer:
    this one fires mid-interview with the interviewer waiting, and giving
    the whole answer there would be a script to read from."""
    sp = _mock()["modes"]["InterviewHint"]["systemPrompt"]
    assert "stuck mid-interview" in sp
    assert "never invent experience they don't have" in sp


# --- item 3: the analysis schema frame --------------------------------


@pytest.mark.parametrize("loc", LOCALES)
def test_the_schema_frame_is_served(loc):
    assert _pp(loc)["analysisSchema"].strip()


@pytest.mark.parametrize("loc", LOCALES)
def test_the_decoder_contract_is_never_translated(loc):
    """Field names and enum values are what the client decodes. Translating
    them would produce a valid-looking Spanish object that fails to parse,
    which is worse than an error because the model would look correct."""
    s = _pp(loc)["analysisSchema"]
    for field in ("title", "sentimentScore", "sentimentLabel", "sentimentEmoji",
                  "sentimentReason", "urgency", "urgencyReason",
                  "personalityMessage", "suggestedTags", "tagReasons"):
        assert f'"{field}"' in s, (loc, field)
    for enum in ("enthusiastic", "collaborative", "informational", "disappointed",
                 "low", "medium", "high", "critical"):
        assert f'"{enum}"' in s, (loc, enum)


@pytest.mark.parametrize("loc", (".es", ".fr"))
def test_the_prose_around_it_is_translated(loc):
    """Matching how analysisPrompt already ships: contract in English,
    instructions in the user's language."""
    s = _pp(loc)["analysisSchema"]
    assert "You analyze meeting transcripts" not in s


# --- item 4: the freeform Ask mode ------------------------------------


@pytest.mark.parametrize("loc", LOCALES)
def test_the_freeform_ask_prompt_is_served(loc):
    assert _pp(loc)["freeformAskPrompt"].strip()


@pytest.mark.parametrize("loc,word", [("", "image"), (".es", "imágenes"),
                                      (".fr", "images")])
def test_ask_still_handles_images_with_no_question(loc, word):
    """The behaviour that made it a literal rather than a preset: it is the
    fallback mode, so it has to answer a question OR describe an image when
    there is no question at all.

    The word is per-locale on purpose. A substring check for "image" passes
    in French and fails in Spanish ("imágenes"), so the generic version was
    testing the translator's word choice rather than the behaviour."""
    assert word in _pp(loc)["freeformAskPrompt"].lower()


def test_ask_is_its_own_field_not_a_preset_mode():
    """defaultPromptModes entries carry name, icon and colour and render as
    chips. Ask is the freeform fallback with none of those, so putting it
    in that list would have added a sixth chip nobody asked for."""
    doc = _pp()
    assert isinstance(doc["defaultPromptModes"], list)
    assert len(doc["defaultPromptModes"]) == 5
    assert isinstance(doc["freeformAskPrompt"], str)


# --- item 5: the follow-up sentence -----------------------------------


@pytest.mark.parametrize("loc", LOCALES)
def test_the_follow_up_sentence_is_folded_in(loc):
    assert "Previous conversation in this chat" in _pp(loc)["analyzeSessionPrompt"]


@pytest.mark.parametrize("loc", (".es", ".fr"))
def test_the_section_header_is_not_translated(loc):
    """The CLIENT emits that literal English string into the user message.
    Translating it would point the model at a section that does not exist,
    and the failure would be a quietly context-less answer rather than an
    error."""
    s = _pp(loc)["analyzeSessionPrompt"]
    assert "'Previous conversation in this chat'" in s


# --- the rule that made this a three-file change ----------------------


def test_every_new_key_shipped_in_every_locale():
    """The sibling and locale-coverage rules. A key present only in base
    means a Spanish or French user silently gets the English behaviour, or
    none, depending on how the client falls back."""
    base = set(_pp().keys())
    for loc in (".es", ".fr"):
        missing = {"analysisSchema", "freeformAskPrompt"} - set(_pp(loc).keys())
        assert not missing, (loc, missing)
    assert {"analysisSchema", "freeformAskPrompt"} <= base
