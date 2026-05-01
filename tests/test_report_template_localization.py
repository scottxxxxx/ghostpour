"""Pin the report-template chrome localization wiring.

Three guarantees we want to lock in:
1. English defaults render when no remote config is provided (degraded
   environments, tests without mocked configs).
2. Locale-specific strings substitute when the matching report-strings.{locale}
   config is present.
3. Falling back from an unsupported locale (e.g., 'fr') to English is silent —
   never produces a `{{strings.X}}` leak in the HTML.

Wire enums (stoplight color, mood) stay English regardless — covered by
test_meeting_report_locale.py for the LLM-side directive.
"""

import json
from pathlib import Path

from app.services.meeting_report import (
    _DEFAULT_REPORT_STRINGS,
    _resolve_report_strings,
    render_report_html,
)


def _minimal_report_json():
    return {
        "header": {"title": "T", "category": "C", "summary": "S", "attendees": []},
        "stoplight": {"color": "green", "label": "L", "detail": "D"},
        "sentiment": {"score": 50, "label": "L", "detail": "D", "arc_narrative": "N", "arc": []},
        "actions": [],
        "technical_issues": [],
        "developments": [],
        "decisions": [],
        "open_questions": [],
        "queries_during_meeting": [],
    }


def _minimal_metadata():
    # project_name must be non-empty — otherwise the masthead block
    # (containing {{strings.header_label}}) gets conditionally removed
    # via _remove_conditional and our header_label assertions miss.
    return {
        "meeting_date": "April 30, 2026",
        "meeting_time": "8:59 AM CDT",
        "meeting_duration": "1h 39m",
        "project_name": "Test Project",
    }


class TestStringResolution:
    def test_no_remote_configs_uses_defaults(self):
        """A degraded environment (e.g. tests) without remote configs must
        not crash — fall back to hardcoded English defaults."""
        strings = _resolve_report_strings(None, locale=None)
        assert strings == _DEFAULT_REPORT_STRINGS

    def test_english_locale_uses_base_config(self):
        """Locale 'en' (or None) → use the base report-strings config."""
        configs = {"report-strings": {"version": 1, "strings": {"header_label": "BASE"}}}
        strings = _resolve_report_strings(configs, locale=None)
        assert strings["header_label"] == "BASE"
        # Other keys fall back to defaults so partial configs don't leave gaps.
        assert strings["sentiment_label"] == _DEFAULT_REPORT_STRINGS["sentiment_label"]

    def test_spanish_locale_uses_es_config(self):
        configs = {
            "report-strings": {"version": 1, "strings": {"header_label": "EN"}},
            "report-strings.es": {"version": 1, "strings": {"header_label": "ES"}},
        }
        strings = _resolve_report_strings(configs, locale="es")
        assert strings["header_label"] == "ES"

    def test_unsupported_locale_falls_back_to_english(self):
        """French isn't shipped — should silently fall back to en."""
        configs = {
            "report-strings": {"version": 1, "strings": {"header_label": "EN"}},
            "report-strings.es": {"version": 1, "strings": {"header_label": "ES"}},
        }
        strings = _resolve_report_strings(configs, locale="fr")
        assert strings["header_label"] == "EN"


class TestRenderedHtmlLocalization:
    def test_english_defaults_render_no_placeholder_leaks(self):
        """Critical: rendered HTML must never contain `{{strings.X}}`. If
        the substitution loop misses a key, this test catches it."""
        html = render_report_html(_minimal_report_json(), _minimal_metadata())
        assert "{{strings." not in html
        assert "Meeting Report" in html  # default English chrome
        assert "Action Required" in html

    def test_spanish_locale_swaps_chrome(self):
        configs = {
            "report-strings": {
                "version": 1,
                "strings": _DEFAULT_REPORT_STRINGS,
            },
            "report-strings.es": {
                "version": 1,
                "strings": {
                    **_DEFAULT_REPORT_STRINGS,
                    "header_label": "Informe de Reunión",
                    "action_required_heading": "Acción requerida",
                    "open_questions_heading": "Preguntas abiertas",
                },
            },
        }
        # Populate open_questions so the section renders — otherwise its
        # header is conditionally suppressed and we can't assert on the
        # localized label.
        rj = _minimal_report_json()
        rj["open_questions"] = [{"question": "Q?", "owner": "Owner"}]
        html = render_report_html(
            rj, _minimal_metadata(),
            remote_configs=configs, locale="es",
        )
        assert "{{strings." not in html
        assert "Informe de Reunión" in html
        assert "Acción requerida" in html
        assert "Preguntas abiertas" in html
        # English chrome must NOT leak through when locale is es.
        # Comments still say "Action Required" — that's fine, they don't
        # render. Just check the visible div text isn't English.
        assert "margin-bottom:16px;\">Action Required</div>" not in html
        # Open Questions heading is what we just localized to "Preguntas
        # abiertas", so we shouldn't see the English form. (The section
        # heading appears in the rendered table since open_questions is non-empty.)
        assert ">Open Questions<" not in html

    def test_japanese_locale_swaps_chrome(self):
        configs = {
            "report-strings.ja": {
                "version": 1,
                "strings": {
                    **_DEFAULT_REPORT_STRINGS,
                    "header_label": "会議レポート",
                    "action_required_heading": "要対応",
                },
            },
        }
        html = render_report_html(
            _minimal_report_json(), _minimal_metadata(),
            remote_configs=configs, locale="ja",
        )
        assert "{{strings." not in html
        assert "会議レポート" in html
        assert "要対応" in html


class TestShippedConfigsAreCompleteAndValid:
    """Pin the on-disk locale configs against the default-strings dict.
    If a translator adds a new key in en but forgets es/ja, this fails."""

    def _load(self, name: str) -> dict:
        path = Path("config/remote") / name
        return json.loads(path.read_text())

    def test_en_config_covers_every_default_key(self):
        cfg = self._load("report-strings.json")
        for key in _DEFAULT_REPORT_STRINGS:
            assert key in cfg["strings"], f"en config missing key: {key}"

    def test_es_config_covers_every_default_key(self):
        cfg = self._load("report-strings.es.json")
        for key in _DEFAULT_REPORT_STRINGS:
            assert key in cfg["strings"], f"es config missing key: {key}"

    def test_ja_config_covers_every_default_key(self):
        cfg = self._load("report-strings.ja.json")
        for key in _DEFAULT_REPORT_STRINGS:
            assert key in cfg["strings"], f"ja config missing key: {key}"
