"""Pin the locale-directive behavior of build_report_prompt.

Wire enums (stoplight color, emoji_label, severity, priority, mood) must
stay English for iOS keying. Only narrative text gets localized.
"""

from app.services.meeting_report import build_report_prompt


def _meeting():
    return {"transcript": "x", "summary": "y", "queries": []}


def test_no_locale_omits_directive():
    system_prompt, _ = build_report_prompt(_meeting(), attendees=["A"])
    assert "LANGUAGE:" not in system_prompt
    assert "BCP-47" not in system_prompt


def test_en_locale_omits_directive():
    """English is the implicit default — no need to nag the model."""
    system_prompt, _ = build_report_prompt(_meeting(), attendees=["A"], locale="en")
    assert "LANGUAGE:" not in system_prompt


def test_es_locale_emits_directive_with_code():
    system_prompt, _ = build_report_prompt(_meeting(), attendees=["A"], locale="es")
    assert "LANGUAGE:" in system_prompt
    assert "'es'" in system_prompt


def test_ja_locale_emits_directive_with_code():
    system_prompt, _ = build_report_prompt(_meeting(), attendees=["A"], locale="ja")
    assert "LANGUAGE:" in system_prompt
    assert "'ja'" in system_prompt


def test_directive_protects_wire_enums():
    """Critical: the directive must explicitly tell the model NOT to translate
    enum values, or iOS keying breaks. Pin every enum we depend on."""
    system_prompt, _ = build_report_prompt(_meeting(), attendees=["A"], locale="es")
    # Stoplight colors
    for color in ("red", "orange", "yellow", "green"):
        assert color in system_prompt, f"stoplight enum '{color}' missing from directive"
    # Emoji labels (iOS uses these as dictionary keys)
    for label in ("enthusiastic", "collaborative", "frustrated", "concerned"):
        assert label in system_prompt, f"emoji_label '{label}' missing from directive"
    # Priority / severity / mood enums
    for enum in ("critical", "standard", "gap", "bug", "risk", "confident", "tense", "concern", "neutral"):
        assert enum in system_prompt, f"enum '{enum}' missing from directive"
