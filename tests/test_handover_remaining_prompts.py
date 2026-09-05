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
# ja ships analysisSchema but not freeformAskPrompt, so it joins only the
# schema tests.
SCHEMA_LOCALES = ("", ".es", ".fr", ".ja")

# Scott's ruling, 2026-09-04: the analysis lane converges on the report
# lane's eight. ShoulderSurf shipped the client half in f6f778d; this is
# GP's served copy kept in step with it, verbatim, so that when the client
# switches its reader to this key the behaviour does not move.
THE_EIGHT = ("informational", "positive", "collaborative", "cautious",
             "pressured", "tense", "disconnected", "decisive")
RETIRED_FIVE = ("enthusiastic", "focused", "frustrated", "concerned", "disappointed")


def _pp(loc=""):
    return json.loads(
        pathlib.Path(f"config/remote/protected-prompts{loc}.json").read_text())


def _mock():
    return json.loads(
        pathlib.Path("config/remote/techrehearsal/mock-interview.json").read_text())


# --- item 2: InterviewHint --------------------------------------------


def test_the_hint_is_the_text_the_user_actually_gets_today():
    """CORRECTED 2026-08-10: the absorption's premise was wrong. TR's client
    flipped InterviewHint promptless on 2026-07-31, so our 90-word prompt WAS
    the live text for ten days, and absorbing the two-paragraph compiled copy
    reverted a live latency fix (TR measured 7.5s at that length). Scott's
    call, same day: restore the 90-word version. This pin now protects the
    text users were actually getting before the absorption."""
    sp = _mock()["modes"]["InterviewHint"]["systemPrompt"]
    assert "90 words" in sp
    assert "Write 2 short paragraphs" not in sp


def test_the_hint_stays_a_nudge_and_not_the_answer():
    """The whole reason it is a separate mode from InterviewModelAnswer:
    this one fires mid-interview with the interviewer waiting, and giving
    the whole answer there would be a script to read from."""
    sp = _mock()["modes"]["InterviewHint"]["systemPrompt"]
    assert "stuck mid-interview" in sp
    assert "never invent experience they don't have" in sp


# --- item 3: the analysis schema frame --------------------------------


@pytest.mark.parametrize("loc", SCHEMA_LOCALES)
def test_the_schema_frame_is_served(loc):
    assert _pp(loc)["analysisSchema"].strip()


@pytest.mark.parametrize("loc", SCHEMA_LOCALES)
def test_the_decoder_contract_is_never_translated(loc):
    """Field names and enum values are what the client decodes. Translating
    them would produce a valid-looking Spanish object that fails to parse,
    which is worse than an error because the model would look correct."""
    s = _pp(loc)["analysisSchema"]
    for field in ("title", "sentimentScore", "sentimentLabel", "sentimentEmoji",
                  "sentimentReason", "urgency", "urgencyReason",
                  "personalityMessage", "suggestedTags", "tagReasons"):
        assert f'"{field}"' in s, (loc, field)
    for enum in (*THE_EIGHT, "low", "medium", "high", "critical"):
        assert f'"{enum}"' in s, (loc, enum)


@pytest.mark.parametrize("loc", SCHEMA_LOCALES)
def test_the_retired_five_are_gone_from_the_enum_lines(loc):
    """The convergence has THREE sites per file and one of them must not
    change. `sentimentLabel` and `sentimentEmoji` list the enum and lose the
    five. `sentimentScore` says "+1.0 (very positive/enthusiastic)", which
    describes a SCALE, not a category, and a find-and-replace eats it and
    silently redefines what +1.0 means.

    So the expected count is asymmetric on purpose: en/es/fr keep exactly one
    retired word (the scale prose, which ships in English), ja keeps zero
    (it translates the scale prose and leaves the enum lines English). One
    number catches both a translated enum and a wrongly edited scale line."""
    s = _pp(loc)["analysisSchema"]
    for word in RETIRED_FIVE:
        assert f'"{word}"' not in s, (loc, word, "still quoted as an enum value")
    hits = sum(s.count(word) for word in RETIRED_FIVE)
    if loc == ".ja":
        assert hits == 0, (loc, hits)
    else:
        assert hits == 1, (loc, hits)
        assert "(very positive/enthusiastic)" in s, loc


@pytest.mark.parametrize("loc", SCHEMA_LOCALES)
def test_the_eight_ship_with_their_definitions(loc):
    """ShoulderSurf's own note on f6f778d: the definitions and contrast
    rules are the load-bearing half. Without them "pressured" fires on any
    meeting that mentions a date. Each label gets a definition bullet with
    the identifier in English, in every locale, and the quote rule for the
    three claims that need a quotable line survives translation."""
    s = _pp(loc)["analysisSchema"]
    for label in THE_EIGHT[1:]:
        assert f'  - "{label}":' in s, (loc, label)
    assert s.count('"informational"') >= 2, (loc, "the DEFAULT line is missing")
    for label in ("tense", "pressured", "disconnected"):
        assert s.count(f'"{label}"') >= 3, (loc, label, "enum, definition, quote rule")


def test_the_english_enum_lines_are_the_client_text_verbatim():
    """Pinned to the lines ShoulderSurf compiles in (f6f778d). If GP's copy
    drifts from theirs, the day they switch readers is the day the wording
    silently changes, which is the exact failure the handover exists to
    prevent."""
    s = _pp()["analysisSchema"]
    assert ('- "sentimentLabel": Exactly one of: "informational", "positive", '
            '"collaborative", "cautious", "pressured", "tense", "disconnected", '
            '"decisive".\n') in s
    assert ('- "sentimentEmoji": A single emoji that represents the sentiment: '
            'informational, positive, collaborative, cautious, pressured, tense, '
            'disconnected, decisive.\n') in s
    assert ('each require a line you could quote: no quote, no claim.\n') in s


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
