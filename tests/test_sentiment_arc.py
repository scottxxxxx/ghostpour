"""Tests for the sentiment-arc fix.

Pre-2026-05-04 the LLM was prompted to emit `value` as a pixel-height
in the 20-48 range, which compressed the apparent variance no matter
how dramatic the meeting was. iOS treated the field as a 0-100
sentiment score, so the in-app arc rendered as a near-flat line near
the bottom of the chart.

Now `value` is on the 0-100 sentiment scale (same as
sentiment.score). The email template scales it to a pixel range via
`_arc_pixel_height`. Tests pin both ends of that contract.
"""

from __future__ import annotations

import re

from app.services import meeting_report


def test_arc_pixel_height_zero_floors_to_4_px():
    assert meeting_report._arc_pixel_height(0) == 4


def test_arc_pixel_height_neutral_50():
    # 4 + 50 * 0.56 = 32
    assert meeting_report._arc_pixel_height(50) == 32


def test_arc_pixel_height_max_100():
    # 4 + 100 * 0.56 = 60
    assert meeting_report._arc_pixel_height(100) == 60


def test_arc_pixel_height_clamps_above_100():
    assert meeting_report._arc_pixel_height(150) == 60


def test_arc_pixel_height_clamps_below_zero():
    assert meeting_report._arc_pixel_height(-20) == 4


def test_arc_pixel_height_handles_non_numeric():
    """Defensive: malformed payload shouldn't break rendering."""
    assert meeting_report._arc_pixel_height(None) == 30
    assert meeting_report._arc_pixel_height("not a number") == 30


# ---------------------------------------------------------------------------
# System-prompt contract — pin the language that pushes the LLM to use range
# ---------------------------------------------------------------------------

def test_prompt_specifies_0_to_100_range():
    """The schema must describe `value` on the 0-100 sentiment scale,
    NOT the legacy 20-48 pixel-height instruction. If this regresses,
    the iOS arc will go flat again."""
    prompt = meeting_report.REPORT_SYSTEM_PROMPT
    user_template = meeting_report.REPORT_USER_TEMPLATE
    # Must NOT instruct pixel heights anymore
    assert "20-48" not in user_template
    assert "bar height in pixels" not in user_template
    # Must explicitly state 0-100 sentiment for the arc value
    assert "0-100" in user_template
    # Must include the explicit range guidance in the rules
    assert "USE THE FULL 0-100 RANGE" in prompt
    assert "casual / amused / mocking" in prompt.lower() or "casual / amused" in prompt.lower()


def test_prompt_calibration_examples_present():
    """The Rules section gives the LLM concrete anchors so it stops
    clustering near 50. Pin a few of those anchors so a future edit
    that softens them doesn't quietly bring back the flat-line issue."""
    p = meeting_report.REPORT_SYSTEM_PROMPT
    # Casual / amused calibration anchor (peaks 70-90)
    assert re.search(r"peaks 70-90", p)
    # Tense / contentious calibration anchor (dips into 15-35)
    assert re.search(r"15-35|20-40", p)
    # The "use full range" bottom-line variance ask
    assert "25-30 points of variance" in p or "30 points of variance" in p
