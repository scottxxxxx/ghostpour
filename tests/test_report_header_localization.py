"""The report masthead is in the report's language, all of it.

Scott's Spanish-phone test, 2026-08-21: the report body was Spanish and
the right-hand header read "INFORME DE REUNIÓN", but the left read
"PROJECT Test" and the date read "August 21, 2026". "Project" was
hardcoded in the template instead of pulled from report-strings, and the
date came from strftime("%B"), which is English in every locale. French
had no report-strings bundle at all.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.services.meeting_report import format_meeting_date, render_report_html

ROOT = Path(__file__).parent.parent
TEMPLATE = (ROOT / "app/static/report_template.html").read_text()


def test_the_template_does_not_hardcode_the_project_label():
    assert ">Project <span" not in TEMPLATE
    assert "{{strings.project_label}}" in TEMPLATE


@pytest.mark.parametrize("loc,expected", [("", "Project"), (".es", "Proyecto"), (".fr", "Projet"), (".ja", "プロジェクト")])
def test_every_report_strings_bundle_carries_project_label(loc, expected):
    d = json.loads((ROOT / f"config/remote/report-strings{loc}.json").read_text())
    assert d["strings"]["project_label"] == expected


def test_french_bundle_has_every_key_english_has():
    en = json.loads((ROOT / "config/remote/report-strings.json").read_text())["strings"]
    fr = json.loads((ROOT / "config/remote/report-strings.fr.json").read_text())["strings"]
    assert set(fr) == set(en)


@pytest.mark.parametrize("locale,expected", [
    (None, "August 21, 2026"), ("en", "August 21, 2026"), ("es", "21 de agosto de 2026"),
    ("es-US", "21 de agosto de 2026"), ("fr", "21 août 2026"), ("ja", "2026年8月21日"),
    ("xx", "August 21, 2026"),
])
def test_masthead_date_is_in_the_report_language(locale, expected):
    assert format_meeting_date(datetime(2026, 8, 21, 14, 43), locale) == expected


def _rc():
    rc = {}
    for loc in ("", ".es", ".fr", ".ja"):
        d = json.loads((ROOT / f"config/remote/report-strings{loc}.json").read_text())
        rc["report-strings" + loc] = d
    return rc


def _report():
    return {"header": {"title": "T", "summary": "S", "category": "c", "attendees": []},
            "stoplight": {"color": "green", "label": "ok", "detail": "d"},
            "sentiment": {"score": 60, "label": "l", "detail": "d", "category": "informational",
                          "category_evidence": "", "arc": [], "arc_narrative": ""},
            "action_items": [], "technical_issues": [], "decisions": [], "open_questions": []}


def test_rendered_spanish_masthead_says_proyecto_not_project():
    meta = {"meeting_date": format_meeting_date(datetime(2026, 8, 21), "es"), "meeting_time": "2:43 PM",
            "meeting_duration": "12m 43s", "project_name": "Test"}
    html = render_report_html(_report(), meta, remote_configs=_rc(), locale="es")
    assert "Proyecto" in html and ">Project <" not in html
    assert "21 de agosto de 2026" in html
