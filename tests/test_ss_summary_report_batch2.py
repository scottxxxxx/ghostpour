"""Batch 2 from the review of 681 real SS calls (2026-10-29).

Summaries: 5 of 8 first-pass summaries of short or non-meeting recordings
wrote decision and action headings filled with "None"; a 42 minute meeting's
consolidation came back nearly empty after its periodic summaries opened
"This transcript does not cover any decisions"; the first-pass recipe had no
decision or owner rule, so proposals were filed as decisions. Template: the
dash rule stripped real hyphens ("hands on", "est ce que").

Report: the schema said category_evidence is empty for ungated categories
while the rules demanded evidence for them; interviews got red and orange
stoplights; a report wrote 2024 deadlines for a 2026 meeting because the
prompt carried no date.
"""
import json
import re
from pathlib import Path

import pytest

from app.services.meeting_report import (
    REPORT_SYSTEM_PROMPT, REPORT_USER_TEMPLATE, build_report_prompt)

ROOT = Path(__file__).resolve().parent.parent / "config" / "remote"
LOCALES = {"en": "protected-prompts.json", "es": "protected-prompts.es.json",
           "fr": "protected-prompts.fr.json", "ja": "protected-prompts.ja.json"}

SHAPE = {"en": "Leave out any section that would be empty",
         "es": "Omite cualquier sección que quedaría vacía",
         "fr": "Omets toute section qui serait vide",
         "ja": "空になるセクションは省いてください。"}
NON_MEETING = {"en": "When the recording is not a working meeting",
               "es": "Cuando la grabación no es una reunión de trabajo",
               "fr": "Quand l'enregistrement n'est pas une réunion de travail",
               "ja": "録音が業務の会議ではない場合"}
DECISION = {"en": "A decision is something the participants agreed",
            "es": "Una decisión es algo que los participantes acordaron",
            "fr": "Une décision est ce dont les participants ont convenu",
            "ja": "決定事項とは参加者が合意したことで"}
OWNER = {"en": "not whoever suggested it.", "es": "no quien la sugirió.",
         "fr": "pas celle qui l'a suggérée.", "ja": "提案しただけの人ではありません。"}
STRETCH = {"en": "never let it remove or replace points from the other summaries",
           "es": "nunca dejes que elimine ni reemplace puntos de los otros resúmenes",
           "fr": "ne le laisse jamais supprimer ni remplacer des points des autres résumés",
           "ja": "その要約によって他の要約の項目を削除したり置き換えたりしないでください。"}
HYPHENS = {"en": "(follow-up, hands-on, vis-à-vis)",
           "es": "(franco-alemán, teórico-práctico, e-mail)",
           "fr": "(est-ce que, peut-être, vis-à-vis, elle-même)"}


def _sp(loc):
    return json.loads((ROOT / LOCALES[loc]).read_text())


@pytest.mark.parametrize("loc", LOCALES)
@pytest.mark.parametrize("kind", ["full", "delta", "consolidation"])
def test_every_summary_kind_drops_empty_sections_and_fits_non_meetings(loc, kind):
    text = _sp(loc)["summaryPrompts"][kind]
    assert SHAPE[loc] in text
    assert NON_MEETING[loc] in text


@pytest.mark.parametrize("loc", LOCALES)
@pytest.mark.parametrize("kind", ["full", "delta", "consolidation"])
def test_every_summary_kind_defines_a_decision_and_an_owner(loc, kind):
    text = _sp(loc)["summaryPrompts"][kind]
    assert DECISION[loc] in text
    assert OWNER[loc] in text


@pytest.mark.parametrize("loc", LOCALES)
def test_consolidation_never_lets_one_empty_stretch_erase_the_rest(loc):
    assert STRETCH[loc] in _sp(loc)["summaryPrompts"]["consolidation"]


@pytest.mark.parametrize("loc", HYPHENS)
def test_the_dash_rule_shows_hyphens_that_stay(loc):
    assert HYPHENS[loc] in _sp(loc)["systemPromptTemplate"]


@pytest.mark.parametrize("loc", LOCALES)
def test_no_dash_punctuation_in_what_this_batch_wrote(loc):
    d = _sp(loc)
    for text in (*d["summaryPrompts"].values(), d["systemPromptTemplate"]):
        assert not re.search("[–—]", text)


# --- report ---------------------------------------------------------------

def test_report_schema_no_longer_forbids_evidence_for_ungated_categories():
    assert "Empty string for every other category" not in REPORT_USER_TEMPLATE
    assert "a verbatim quote that supports it when one exists" in REPORT_USER_TEMPLATE


def test_report_stoplight_does_not_rate_an_interview_as_risk():
    assert "An interview, a lecture, a class, or a social or personal conversation has nothing being delivered: use green" in REPORT_SYSTEM_PROMPT


def test_report_decisions_and_open_question_owners():
    assert "is an open question, not a decision." in REPORT_SYSTEM_PROMPT
    assert "leave owner as an empty string" in REPORT_SYSTEM_PROMPT


def test_report_carries_the_meeting_date_and_resolves_against_it():
    assert "Resolve relative dates" in REPORT_SYSTEM_PROMPT
    _, user = build_report_prompt({"transcript": "t", "summary": "s"}, attendees=["A"],
                                  meeting_date="Thursday 2026-10-29")
    assert "MEETING DATE:\nThursday 2026-10-29\n" in user
    _, user = build_report_prompt({"transcript": "t", "summary": "s"}, attendees=["A"])
    assert "MEETING DATE:\n(not given)\n" in user


def test_the_report_route_forwards_the_meetings_own_date(client, pro_user, tmp_db_path, monkeypatch):
    """Request side at the route: the date the client sent is the date in the
    prompt, so a report regenerated days later still resolves "Friday" from
    the meeting."""
    from tests.test_report_language_on_generate import _post, _seed, _wire
    calls = _wire(monkeypatch)
    mid = _seed(tmp_db_path, pro_user["user_id"])
    r = _post(client, pro_user, mid, meeting_start_iso="2026-08-11T13:01:00-05:00")
    assert r.status_code == 200, r.text
    assert "MEETING DATE:\nTuesday 2026-08-11\n" in calls[-1].user_content
