"""S-Curve sheet: cumulative planned work against reported progress.

Three series that are deliberately not the same kind of thing:

  Baseline  what the FIRST plan version said, static, because editing
            today's dates must not rewrite what was promised.
  Planned   what the CURRENT plan says, live formulas over the Gantt
            View's own date cells so it redraws like the bars do.
  Reported  duration-weighted percent complete as of each meeting, held
            flat in between because that is genuinely all we know.

All three are weighted by working days counted inclusively, matching
Excel's NETWORKDAYS, so the Python-computed history and the live formulas
weight the same work the same way.
"""

from __future__ import annotations

import datetime
import io
import shutil
import subprocess

import openpyxl
import pytest

from app.services.doc_templates import (
    _planned_share,
    _wd_inclusive,
    _weighted_progress,
    render_gantt,
    render_gantt_detailed,
)

D = datetime.date.fromisoformat

_PLAN = {
    "project": "Field Kit",
    "meeting_date": "2026-07-27",
    "tasks": [
        {"id": 1, "name": "Phase", "type": "phase", "parent_id": None,
         "owner": None, "status": "in_progress", "start": "2026-07-06",
         "end": "2026-07-31", "depends_on": []},
        {"id": 2, "name": "A", "type": "task", "parent_id": 1, "owner": None,
         "status": "complete", "start": "2026-07-06", "end": "2026-07-10",
         "depends_on": [], "percent_complete": 100},
        {"id": 3, "name": "B", "type": "task", "parent_id": 1, "owner": None,
         "status": "in_progress", "start": "2026-07-13", "end": "2026-07-24",
         "depends_on": [2], "percent_complete": 50},
        {"id": 4, "name": "C", "type": "task", "parent_id": 1, "owner": None,
         "status": "not_started", "start": "2026-07-27", "end": "2026-07-31",
         "depends_on": [3]},
    ],
}

_HISTORY = [{"as_of": "2026-07-13", "tasks": [
    {"id": 2, "name": "A", "type": "task", "start": "2026-07-06",
     "end": "2026-07-10", "percent_complete": 60},
    {"id": 3, "name": "B", "type": "task", "start": "2026-07-13",
     "end": "2026-07-22", "percent_complete": 0},
    {"id": 4, "name": "C", "type": "task", "start": "2026-07-23",
     "end": "2026-07-29"},
]}]


def _book(history=_HISTORY):
    blob = render_gantt_detailed(_PLAN, today=D("2026-07-27"), history=history)
    return openpyxl.load_workbook(io.BytesIO(blob))


# --- counting convention --------------------------------------------------

def test_working_days_are_counted_inclusively_like_networkdays():
    assert _wd_inclusive(D("2026-07-06"), D("2026-07-10")) == 5   # Mon..Fri
    assert _wd_inclusive(D("2026-07-06"), D("2026-07-06")) == 1
    assert _wd_inclusive(D("2026-07-06"), D("2026-07-13")) == 6   # skips a weekend
    assert _wd_inclusive(D("2026-07-13"), D("2026-07-06")) == 0   # inverted


def test_progress_uses_the_same_rule_as_the_phase_rollups():
    tasks = [
        {"type": "task", "start": "2026-07-06", "end": "2026-07-10",
         "percent_complete": 50},                       # 5 wd at 50%
        {"type": "task", "start": "2026-07-13", "end": "2026-07-17",
         "status": "complete"},                         # 5 wd, Complete -> 100
        {"type": "task", "start": "2026-07-20", "end": "2026-07-24"},
    ]                                                   # 5 wd, unstated -> 0
    assert _weighted_progress(tasks) == pytest.approx((2.5 + 5) / 15)


def test_milestones_and_phases_carry_no_weight():
    tasks = [
        {"type": "task", "start": "2026-07-06", "end": "2026-07-10",
         "percent_complete": 0},
        {"type": "milestone", "start": "2026-07-13", "end": "2026-07-13",
         "status": "complete"},
        {"type": "phase", "start": "2026-07-06", "end": "2026-07-13",
         "percent_complete": 100},
    ]
    # a complete milestone must not drag the curve up on its own
    assert _weighted_progress(tasks) == 0


def test_planned_share_walks_from_zero_to_one():
    tasks = _PLAN["tasks"]
    assert _planned_share(tasks, D("2026-07-01")) == 0
    assert _planned_share(tasks, D("2026-07-10")) == pytest.approx(0.25)
    assert _planned_share(tasks, D("2026-08-31")) == 1


# --- the sheet ------------------------------------------------------------

def test_sheet_is_added_to_the_detailed_style_only():
    assert _book().sheetnames == ["Gantt View", "Slip", "Receipts", "S-Curve"]
    simple = openpyxl.load_workbook(io.BytesIO(
        render_gantt(_PLAN, today=D("2026-07-27"))))
    assert simple.sheetnames == ["Gantt View"]


def test_planned_is_live_off_the_gantt_view_dates():
    sc = _book()["S-Curve"]
    f = sc.cell(5, 3).value
    assert isinstance(f, str) and f.startswith("=IFERROR(")
    assert "'Gantt View'!$E" in f and "NETWORKDAYS" in f, f
    assert "$H$1" in f, "shares one live denominator"


def test_baseline_is_static_history_not_a_formula():
    sc = _book()["S-Curve"]
    v = sc.cell(5, 2).value
    assert isinstance(v, float), "editing today's plan must not rewrite it"
    assert 0 <= v <= 1


def test_reported_holds_flat_between_meetings_and_is_blank_before_the_first():
    sc = _book()["S-Curve"]
    col = [sc.cell(r, 4).value for r in range(5, 5 + 5)]
    assert col[0] is None, "nothing reported before the first meeting we have"
    # 2026-07-13 meeting, then the current plan dated 2026-07-27
    assert col[1] == pytest.approx(1 / 6)
    assert col[2] == pytest.approx(1 / 6), "held flat, never interpolated"
    assert col[3] == pytest.approx(0.5)


def test_a_first_ever_plan_says_why_the_baseline_is_missing():
    sc = _book(history=None)["S-Curve"]
    assert sc.cell(5, 2).value is None
    text = " ".join(str(sc.cell(r, 1).value or "") for r in range(1, sc.max_row + 1))
    assert "second plan version" in text


def test_chart_exists_with_all_three_series():
    sc = _book()["S-Curve"]
    assert len(sc._charts) == 1
    # three lines plus the marker-only series that dots the weeks a
    # meeting actually happened in (2026-07-30 review pass)
    assert len(sc._charts[0].series) == 4
    assert sc._charts[0].series[3].marker.symbol == "circle"


def test_header_explains_which_lines_move_and_which_do_not():
    sc = _book()["S-Curve"]
    note = str(sc["A2"].value)
    assert "never moves" in note
    assert "redraws" in note
    assert "held flat" in note


# --- behavior, via a real formula engine ----------------------------------

_SOFFICE = shutil.which("soffice")


@pytest.mark.skipif(_SOFFICE is None, reason="needs LibreOffice to recalculate")
def test_recalculated_planned_curve_matches_hand_computed_shares(tmp_path):
    """A is 5 working days, B is 10, C is 5, so 20 total. The planned curve
    should reach a quarter, a half, three quarters and then all of it."""
    src = tmp_path / "curve.xlsx"
    src.write_bytes(render_gantt_detailed(
        _PLAN, today=D("2026-07-27"), history=_HISTORY))
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(
        [_SOFFICE, "--headless", "--convert-to", "xlsx",
         str(src), "--outdir", str(out)],
        check=True, capture_output=True, timeout=240,
    )
    sc = openpyxl.load_workbook(out / "curve.xlsx", data_only=True)["S-Curve"]
    assert sc["H1"].value == 20, "total scheduled working days"
    planned = [sc.cell(r, 3).value for r in range(5, 9)]
    assert planned == pytest.approx([0.25, 0.5, 0.75, 1.0])
    # the baseline plan was a different shape, which is the whole point
    assert sc.cell(5, 2).value == pytest.approx(5 / 18)
