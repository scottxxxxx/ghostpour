"""Pin canned-report locale resolution.

The CTA banner inside the placeholder/sample report localizes per
canned-report.{locale} variants. Body narrative stays English (sample
meeting content is fixed across locales).

The wiring lives in reports.py::_build_canned_report_response — these
tests inspect it indirectly via on-disk config validity, plus a unit
test on the resolution function once we factor it out. For now: pin
that all three locale configs are present, valid, and have the
required CTA fields.
"""

import json
from pathlib import Path


_CTA_FIELDS = {
    "kind", "eyebrow", "headline", "body", "button_text", "action", "pill_text",
}


def _load(name: str) -> dict:
    return json.loads((Path("config/remote") / name).read_text())


class TestCannedReportConfigs:
    def test_english_base_config_has_required_cta_fields(self):
        cfg = _load("canned-report.json")
        assert "report_html_template" in cfg
        for f in _CTA_FIELDS:
            assert f in cfg["cta"], f"en config missing cta.{f}"

    def test_spanish_config_has_required_cta_fields(self):
        cfg = _load("canned-report.es.json")
        assert "report_html_template" in cfg
        for f in _CTA_FIELDS:
            assert f in cfg["cta"], f"es config missing cta.{f}"

    def test_japanese_config_has_required_cta_fields(self):
        cfg = _load("canned-report.ja.json")
        assert "report_html_template" in cfg
        for f in _CTA_FIELDS:
            assert f in cfg["cta"], f"ja config missing cta.{f}"

    def test_locale_variants_use_native_script(self):
        """Smoke check the translations actually got translated. If a
        translator paste-overs English content into a locale variant
        (real failure mode), the CTA copy never makes it to users."""
        es = _load("canned-report.es.json")
        ja = _load("canned-report.ja.json")
        # Spanish-specific marker
        assert "Plus" in es["cta"]["headline"]
        assert "Actualiza" in es["cta"]["pill_text"] or "Actualizar" in es["cta"]["button_text"]
        # Japanese-specific marker (hiragana/katakana presence)
        assert any(0x3040 <= ord(c) <= 0x30FF for c in ja["cta"]["headline"]), \
            "Japanese headline appears to be missing kana — translator may have shipped English"

    def test_action_is_stable_across_locales(self):
        """cta.action drives iOS routing and MUST stay stable per the
        wire contract — never localize this field."""
        en = _load("canned-report.json")
        es = _load("canned-report.es.json")
        ja = _load("canned-report.ja.json")
        assert en["cta"]["action"] == es["cta"]["action"] == ja["cta"]["action"] == "open_paywall"

    def test_kind_is_stable_across_locales(self):
        """cta.kind is a wire enum — never localize."""
        en = _load("canned-report.json")
        es = _load("canned-report.es.json")
        ja = _load("canned-report.ja.json")
        assert en["cta"]["kind"] == es["cta"]["kind"] == ja["cta"]["kind"]
