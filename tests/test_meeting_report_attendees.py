"""Attendee fallback from transcript speaker labels.

"Identified Speakers" comes from the app-supplied CONFIRMED ATTENDEES list.
When the app omits it, a fully-labeled transcript (speakers diarized inline as
`[Ravi Varma]`) used to render as "No named speakers identified". The fallback
recovers those named labels so the meeting doesn't read as anonymous. Anonymous
`Speaker N` labels and non-person annotations are excluded.
"""

from __future__ import annotations

from app.services.meeting_report import (
    _extract_named_speakers_from_transcript,
    build_report_prompt,
)

# Shape mirrors a real ShoulderSurf transcript: inline "[Label] text" turns,
# a mix of named speakers, anonymous "[Speaker N]", and a "[Multiple]" crosstalk.
TOWNHALL = (
    "[Speaker 1] Okay, let's get started, everyone.\n"
    "[Ravi Varma] Thanks. First I want to cover the Artemis migration.\n"
    "[Mike DiTroia] On support, we're standing up tiered response teams.\n"
    "[Ravi Varma] Right, and ownership stays with the named FDE.\n"
    "[Multiple] (crosstalk)\n"
    "[Srinivas Pakala] The partner framework is still maturing.\n"
    "[Speaker 10] Quick question on the Thailand partnership.\n"
    "[Girish] I'll take that one.\n"
)


def test_extracts_named_speakers_in_order_deduped():
    assert _extract_named_speakers_from_transcript(TOWNHALL) == [
        "Ravi Varma",
        "Mike DiTroia",
        "Srinivas Pakala",
        "Girish",
    ]


def test_excludes_anonymous_and_non_person_labels():
    out = _extract_named_speakers_from_transcript(TOWNHALL)
    assert not any(o.lower().startswith("speaker") for o in out)
    assert "Multiple" not in out


def test_excludes_annotation_brackets():
    t = "[Laughter]\n[Inaudible]\n[Crosstalk]\n[Music]\n[Priya] Let's begin.\n"
    assert _extract_named_speakers_from_transcript(t) == ["Priya"]


def test_empty_or_anonymous_only_returns_empty():
    assert _extract_named_speakers_from_transcript("") == []
    assert _extract_named_speakers_from_transcript(None) == []
    assert _extract_named_speakers_from_transcript(
        "[Speaker 1] hi\n[Speaker 2] hello\n[Multiple] (crosstalk)"
    ) == []


def test_prompt_falls_back_to_transcript_names_when_attendees_empty():
    _, user = build_report_prompt({"transcript": TOWNHALL, "summary": "s"}, attendees=None)
    assert "No named speakers identified" not in user
    assert "- Ravi Varma" in user
    assert "- Mike DiTroia" in user


def test_prompt_uses_sentinel_when_nothing_identifiable():
    anon = "[Speaker 1] hi\n[Speaker 2] hello"
    _, user = build_report_prompt({"transcript": anon, "summary": "s"}, attendees=None)
    assert "No named speakers identified" in user


def test_prompt_prefers_confirmed_list_over_transcript():
    """App-supplied attendees win; the transcript is not consulted."""
    _, user = build_report_prompt({"transcript": TOWNHALL, "summary": "s"}, attendees=["Scott", "Mana"])
    assert "- Scott" in user
    assert "- Mana" in user
    # transcript-derived names must not leak in when the app gave a list
    assert "- Ravi Varma" not in user
