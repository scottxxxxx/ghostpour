"""Document template registry (phase 2 pilot: Smartsheet-style Gantt).

The design lesson from the live prompt-only experiment (2026-07-12): the
model followed the spec where it got hex codes and improvised everywhere
else, took ~8 minutes, and will drift run to run. So the LLM never draws.
A template turn asks the model for STRUCTURED JSON ONLY (one cheap text
turn, no sandbox), and a deterministic renderer here draws the identical
file every time — seconds, pennies, byte-stable styling.

Registry entries pair an extraction prompt with a renderer; the two
version together, which is why the schema lives HERE and never in a
client prompt. Triage: the intent classifier's ask matches template
hints; the offer proposes the template; a confirm routes to this lane;
anything custom falls through to ad-hoc sandbox generation, which stays
the never-locked-in fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

logger = logging.getLogger("ghostpour.doc_templates")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_GANTT_SCHEMA_PROMPT = (
    "Extract this project's plan from the conversation and meeting content "
    "as JSON ONLY — no prose, no code fences. Schema: {\"project\": str, "
    "\"meeting_date\": \"YYYY-MM-DD\"|null, "
    "\"tasks\": [{\"id\": int, \"name\": str, \"type\": \"phase\"|\"task\"|"
    "\"milestone\", \"parent_id\": int|null, \"owner\": str|null, "
    "\"status\": \"complete\"|\"in_progress\"|\"on_hold\"|\"not_started\"|"
    "\"blocked\", \"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\", "
    "\"depends_on\": [int]}]}. Rules: phases have parent_id null; tasks and "
    "milestones carry the id of their phase; milestones have start equal to "
    "end; dates must be consistent with dependencies (a task never starts "
    "before its predecessor ends); owner is the person's name as spoken; "
    "meeting_date is the date of the meeting this plan comes from as stated "
    "in the content (the most recent one when several), null when no date "
    "is evident. "
    "Extract every task and milestone discussed. Output only the JSON object."
)

_GANTT_DETAILED_SCHEMA_PROMPT = (
    "Extract this project's plan from the conversation and meeting content "
    "as JSON ONLY, no prose, no code fences. Schema: {\"project\": str, "
    "\"meeting_date\": \"YYYY-MM-DD\"|null, "
    "\"tasks\": [{\"id\": int, \"name\": str, \"type\": \"phase\"|\"task\"|"
    "\"milestone\", \"parent_id\": int|null, \"owner\": str|null, "
    "\"status\": \"complete\"|\"in_progress\"|\"on_hold\"|\"not_started\"|"
    "\"blocked\", \"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\", "
    "\"depends_on\": [int], \"percent_complete\": int|null, "
    "\"effort\": str|null, \"evidence\": [{\"field\": str, \"quote\": str, "
    "\"speaker\": str|null}]}]}. Rules: phases have parent_id null; tasks "
    "and milestones carry the id of their phase; milestones have start "
    "equal to end; dates must be consistent with dependencies (a task "
    "never starts before its predecessor ends); owner is the person's name "
    "as spoken; meeting_date is the date of the meeting this plan comes "
    "from as stated in the content (the most recent one when several), "
    "null when no date is evident. percent_complete and effort are "
    "STRICTLY what a person stated in the content (\"about 80 percent\" "
    "is 80; \"two days of work\" is \"2 days\"): when nobody stated a "
    "value, use null. Never estimate, and never infer a percent from "
    "status. evidence lists short verbatim quotes from the content that "
    "support extracted values (dates, status, percent_complete, effort, "
    "owner), with field naming which value each quote supports; include "
    "speaker when identifiable; omit evidence you do not have rather than "
    "paraphrasing. Extract every task and milestone discussed. Output "
    "only the JSON object."
)

# palette lifted from the reference artifact (ABM_Gantt_Smartsheet_Style)
_C = {
    "bar": "A8B9C9", "summary": "6E7B8A", "project": "3D4653",
    "weekend": "F3F3F3", "today": "FFF6DE", "risk": "E0341E",
    "grid": "E9E9E9", "white": "FFFFFF",
    "status": {"complete": "1F4E9C", "in_progress": "2E9E4F",
               "on_hold": "F5A623", "not_started": "E0341E",
               "blocked": "E0341E"},
    "chips": ["1F4E9C", "2E9E4F", "F5A623", "D35400", "7B4EA3", "2C7A7B"],
}


def _d(s):
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _dep_code(pred: dict, succ: dict) -> str:
    """Two-letter dependency nomenclature, DERIVED from the extracted
    dates — never asked of the model (Scott 2026-07-15: the model would
    invent the minority types; dates it already committed to are
    arithmetic). Starts align -> SS, ends align -> FF, everything else —
    including anything ambiguous — defaults to FS. The user can always
    correct a cell."""
    if _d(succ["start"]) == _d(pred["start"]):
        return "SS"
    if _d(succ["end"]) == _d(pred["end"]):
        return "FF"
    return "FS"


_STATUS_LABELS = {
    "complete": "Complete", "in_progress": "In Progress",
    "on_hold": "On Hold", "not_started": "Not Started", "blocked": "Blocked",
}


def render_gantt(data: dict, *, today: date | None = None,
                 history: list[dict] | None = None) -> bytes:
    """Deterministic Smartsheet-style Gantt from extracted plan JSON.
    `history` is accepted for renderer-signature parity (the template
    lane passes it to every renderer) and unused by the simple style."""
    return _serialize_wb(_build_gantt_wb(data, today=today))


def _build_gantt_wb(data: dict, *, today: date | None = None,
                    detail_cols: bool = False):
    """Build the Gantt View workbook (shared by the simple and detailed
    renderers; the detailed one appends sheets to the same workbook).

    detail_cols (detailed style only): adds % Done and Effort columns
    BETWEEN Assigned To and the day grid, and a live completed-portion
    overlay on each bar driven by the % cell (edit the percent in Excel
    and the done-portion redraws, same live-grid rule as everything
    else). Column positions A-I are unchanged so every $D/$E/$F formula
    is shared between styles; only the day-grid origin shifts.

    LIVE GRID (Scott 2026-07-15): the timeline bars are conditional
    formatting formulas over real date cells, not painted fills — edit a
    Start/End date in Excel and the bar redraws; flip the Status dropdown
    and the dot recolors; the today column tracks TODAY(). Row 1 is a
    hidden axis of real dates the formulas compare against (the axis
    itself is fixed at build; bars clip at its edges). Weak viewers that
    skip conditional formatting show a plain grid — real Excel and Google
    Sheets render fully."""
    import openpyxl
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    def fill(hex6):
        return PatternFill("solid", fgColor="FF" + hex6)

    def dxf_fill(hex6):
        return PatternFill(start_color="FF" + hex6, end_color="FF" + hex6,
                           fill_type="solid")

    tasks = data.get("tasks") or []
    if not tasks:
        raise ValueError("no tasks extracted")
    today = today or date.today()
    start = min(_d(t["start"]) for t in tasks)
    end = max(_d(t["end"]) for t in tasks)
    # +14 days of runway so a user can push dates right and the bars
    # still have axis to land on
    days = [(start + timedelta(n))
            for n in range((end - start).days + 15)][:180]

    by_id = {t["id"]: t for t in tasks}
    phases = [t for t in tasks if t.get("type") == "phase"]
    children: dict = {p["id"]: [] for p in phases}
    for t in tasks:
        if t.get("type") != "phase" and t.get("parent_id") in children:
            children[t["parent_id"]].append(t)

    def at_risk(t):
        return t["status"] == "blocked" or (
            t["status"] == "not_started" and _d(t["start"]) < today)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gantt View"
    # A:dot B:name C:risk D:status E:start F:end G:predecessors H:chip I:owner
    FIRST_DAY_COL = 12 if detail_cols else 10
    PCT_COL = "J"   # % Done, only written when detail_cols
    KEY_TOP = 4          # status key block under the 3 header rows
    first_bar_row = KEY_TOP + 6   # key rows + blank + project row

    # Pre-pass: worksheet row of every task, so Predecessors can cite
    # rows in either direction (forward deps included).
    row_of: dict = {}
    r = first_bar_row
    for phase in phases:
        r += 1
        row_of[phase["id"]] = r
        for t in children.get(phase["id"], []):
            r += 1
            row_of[t["id"]] = r
    last_row = r
    last_col = FIRST_DAY_COL + len(days) - 1
    grid = lambda row: (f"{get_column_letter(FIRST_DAY_COL)}{row}:"  # noqa: E731
                        f"{get_column_letter(last_col)}{row}")

    # Row 1 (hidden): the real-date axis every bar formula compares
    # against. Rows 2-3: week-of labels + day letters (visual only).
    for i, d in enumerate(days):
        col = FIRST_DAY_COL + i
        ws.column_dimensions[get_column_letter(col)].width = 3
        ax = ws.cell(1, col, d)
        ax.number_format = "yyyy-mm-dd"
        if d.weekday() == 0:
            c = ws.cell(2, col, f"Week of {d.strftime('%b %d')}")
            c.font = Font(size=8, bold=True)
        letter = "MTWTFSS"[d.weekday()]
        c = ws.cell(3, col, letter)
        c.alignment = Alignment(horizontal="center")
        c.font = Font(size=8, bold=True,
                      color="FF" + (_C["risk"] if d.weekday() >= 5 else "3D4653"))
        if d == today:
            c.font = Font(size=8, bold=True, color="FF" + _C["risk"])
    ws.row_dimensions[1].hidden = True
    _heads = [("", 7), ("Task Name", 38), ("At\nRisk", 5), ("Status", 12),
              ("Start\nDate", 11), ("End\nDate", 11), ("Predecessors", 12),
              ("", 4), ("Assigned To", 16)]
    if detail_cols:
        _heads += [("%\nDone", 7), ("Effort", 10)]
    for col, (head, width) in enumerate(_heads, start=1):
        c = ws.cell(3, col, head)
        c.font = Font(bold=True, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.fill = fill("E9E9E9")   # header band (Scott: "lost the cool highlighting")
        ws.column_dimensions[get_column_letter(col)].width = width

    # status key block — amber header band like the reference
    row = KEY_TOP
    kc = ws.cell(row, 2, "  STATUS KEY")
    kc.font = Font(bold=True, size=9)
    kc.fill = fill("FDF3E3")
    ws.cell(row, 1).fill = fill("F5A623")
    for label, key in (("COMPLETED", "complete"), ("IN PROGRESS", "in_progress"),
                       ("ON HOLD", "on_hold"), ("NOT STARTED", "not_started")):
        row += 1
        ws.cell(row, 1, "●").font = Font(color="FF" + _C["status"][key], size=11)
        ws.cell(row, 2, f"      {label}").font = Font(size=8)
    row += 2

    def date_cells(r, s, e, hex_color=None, size=8):
        for col, d in ((5, s), (6, e)):
            c = ws.cell(r, col, d)
            c.number_format = "yyyy-mm-dd"
            c.font = Font(size=size,
                          color="FF" + (hex_color or "3D4653"))

    def bar_rules(r, bar_hex, risk_aware=False, pct_overlay=False):
        """The live bars, drawn twice from the same date cells so every
        viewer shows them (Scott's Numbers finding 2026-07-16: Numbers
        computes formulas but refuses conditional FORMATTING, so
        fill-only bars vanished there):

        1. A full-block character (█) via per-cell formula, font-colored
           to the bar — Numbers renders colored character bars; in Excel
           the same-color character melts invisibly into the fill.
        2. Conditional-formatting fills for Excel/Sheets, with the FONT
           recolored in the same rule so a live status flip (risk red)
           recolors the characters too and nothing clashes.
        """
        E, F = f"$E{r}", f"$F{r}"
        ax = f"{get_column_letter(FIRST_DAY_COL)}$1"
        in_range = f"AND({ax}>={E},{ax}<={F})"
        for i in range(len(days)):
            col = FIRST_DAY_COL + i
            L = get_column_letter(col)
            c = ws.cell(r, col, f'=IF(AND({L}$1>={E},{L}$1<={F}),"█","")')
            c.font = Font(color="FF" + bar_hex, size=9)
            c.alignment = Alignment(horizontal="center")
        if risk_aware:
            risky = (f"AND({ax}>={E},{ax}<={F},OR($D{r}=\"Blocked\","
                     f"AND($D{r}=\"Not Started\",{E}<TODAY())))")
            ws.conditional_formatting.add(grid(r), FormulaRule(
                formula=[risky], fill=dxf_fill(_C["risk"]),
                font=Font(color="FF" + _C["risk"]), stopIfTrue=True))
        if pct_overlay:
            # Completed-portion overlay (detailed style): the leading
            # share of the bar recolors to the status-complete blue,
            # driven live by the % Done cell. Added BEFORE the base bar
            # rule so it wins where both match; blank % means no
            # overlay and the plain bar shows.
            P = f"${PCT_COL}{r}"
            done_f = (f"AND({P}<>\"\",{ax}>={E},"
                      f"{ax}<={E}+({F}-{E})*{P})")
            ws.conditional_formatting.add(grid(r), FormulaRule(
                formula=[done_f], fill=dxf_fill(_C["status"]["complete"]),
                font=Font(color="FF" + _C["status"]["complete"]),
                stopIfTrue=True))
        ws.conditional_formatting.add(grid(r), FormulaRule(
            formula=[in_range], fill=dxf_fill(bar_hex),
            font=Font(color="FF" + bar_hex), stopIfTrue=True))

    # project row
    ws.cell(row, 2, f"  {data.get('project') or 'Project'}").font = \
        Font(bold=True, color="FFFFFFFF")
    for c in range(1, FIRST_DAY_COL):
        ws.cell(row, c).fill = fill(_C["project"])
    date_cells(row, start, end, hex_color="FFFFFF", size=9)
    ws.row_dimensions[row].height = 12
    bar_rules(row, _C["summary"])
    row += 1

    dv = DataValidation(
        type="list",
        formula1='"Complete,In Progress,On Hold,Not Started,Blocked"',
        allow_blank=True)
    ws.add_data_validation(dv)

    chip_cache: dict = {}
    for phase in phases:
        assert row == row_of[phase["id"]]
        ws.cell(row, 1, "●").font = Font(
            color="FF" + _C["status"].get(phase.get("status", "in_progress"), _C["bar"]))
        ws.cell(row, 2, f"  −  {phase['name']}").font = Font(bold=True, size=9)
        ws.cell(row, 4, _STATUS_LABELS.get(phase.get("status", ""), "")).font = Font(size=8)
        date_cells(row, _d(phase["start"]), _d(phase["end"]))
        ws.row_dimensions[row].outline_level = 1
        ws.row_dimensions[row].height = 12
        bar_rules(row, _C["summary"])
        dv.add(f"D{row}")
        row += 1
        for t in children.get(phase["id"], []):
            assert row == row_of[t["id"]]
            risky = at_risk(t)
            text_hex = _C["risk"] if risky else "3D4653"
            ws.cell(row, 1, "●" if t["type"] != "milestone" else "").font = \
                Font(color="FF" + _C["status"].get(t["status"], _C["bar"]))
            name = ("          🏁 " if t["type"] == "milestone" else "          ") + t["name"]
            ws.cell(row, 2, name).font = Font(size=9, color="FF" + text_hex)
            fl = ws.cell(row, 3, "⚑" if risky else "⚐")
            fl.font = Font(color="FF" + (_C["risk"] if risky else "9AA4AF"))
            fl.alignment = Alignment(horizontal="center")
            st = ws.cell(row, 4, _STATUS_LABELS.get(t.get("status", ""), ""))
            st.font = Font(size=8, color="FF" + text_hex)
            dv.add(f"D{row}")
            date_cells(row, _d(t["start"]), _d(t["end"]), hex_color=text_hex)
            # Predecessors: Smartsheet nomenclature, dates-derived (FS
            # default; SS/FF only when the extracted dates say so)
            codes = ", ".join(
                f"{row_of[dep]}{_dep_code(by_id[dep], t)}"
                for dep in (t.get("depends_on") or []) if dep in by_id and dep in row_of)
            if codes:
                pc = ws.cell(row, 7, codes)
                pc.font = Font(size=8, color="FF" + text_hex)
                pc.alignment = Alignment(horizontal="center")
            owner = (t.get("owner") or "").strip()
            if owner:
                # chip = colored initials; FULL NAME beside it (Scott's
                # review: "I don't get the full name, just the letter")
                initials = "".join(w[0] for w in owner.split()[:2]).upper()
                hex_c = chip_cache.setdefault(
                    owner, _C["chips"][int(hashlib.sha256(owner.encode()).hexdigest(), 16) % len(_C["chips"])])
                chip = ws.cell(row, 8, initials)
                chip.fill = fill(hex_c)
                chip.font = Font(bold=True, size=8, color="FFFFFFFF")
                chip.alignment = Alignment(horizontal="center")
                nm = ws.cell(row, 9, owner)
                nm.font = Font(size=8, color="FF" + text_hex)
            if detail_cols:
                pct = t.get("percent_complete")
                if isinstance(pct, int) and 0 <= pct <= 100:
                    pcell = ws.cell(row, 10, pct / 100)
                    pcell.number_format = "0%"
                    pcell.font = Font(size=8, color="FF" + text_hex)
                    pcell.alignment = Alignment(horizontal="center")
                eff = t.get("effort")
                if isinstance(eff, str) and eff.strip():
                    ecell = ws.cell(row, 11, eff.strip())
                    ecell.font = Font(size=8, color="FF" + text_hex)
                    ecell.alignment = Alignment(horizontal="center")
            ws.row_dimensions[row].outline_level = 2
            if t["type"] == "milestone":
                # The ◆ marker is a formula so it moves with the date —
                # SAME formula shape as the bar cells (Scott 2026-07-16:
                # Excel's inconsistent-formula check stamped green
                # triangles along every milestone row because its formula
                # differed from its neighbors'). A milestone's start
                # equals its end, so the range test marks exactly one day.
                for i in range(len(days)):
                    col = FIRST_DAY_COL + i
                    L = get_column_letter(col)
                    m = ws.cell(row, col,
                                f'=IF(AND({L}$1>=$E{row},{L}$1<=$F{row}),'
                                f'"◆","")')
                    m.font = Font(color="FF" + _C["risk"], bold=True)
                    m.alignment = Alignment(horizontal="center")
            else:
                bar_rules(row, _C["bar"], risk_aware=True,
                          pct_overlay=detail_cols)
            row += 1

    # grid-wide dynamics AFTER the bar rules so bars win: the today
    # column tracks TODAY(); weekends shade by formula
    ax0 = f"{get_column_letter(FIRST_DAY_COL)}$1"
    full_grid = (f"{get_column_letter(FIRST_DAY_COL)}{first_bar_row}:"
                 f"{get_column_letter(last_col)}{last_row}")
    ws.conditional_formatting.add(full_grid, FormulaRule(
        formula=[f"{ax0}=TODAY()"], fill=dxf_fill(_C["today"]), stopIfTrue=True))
    ws.conditional_formatting.add(full_grid, FormulaRule(
        formula=[f"WEEKDAY({ax0},2)>5"], fill=dxf_fill(_C["weekend"])))
    # live status dots: flipping the Status dropdown recolors column A
    dot_range = f"A{first_bar_row + 1}:A{last_row}"
    for key, label in _STATUS_LABELS.items():
        ws.conditional_formatting.add(dot_range, FormulaRule(
            formula=[f'$D{first_bar_row + 1}="{label}"'],
            font=Font(color="FF" + _C["status"].get(key, _C["bar"]))))

    ws.freeze_panes = f"{get_column_letter(FIRST_DAY_COL)}4"
    ws.sheet_properties.outlinePr.summaryBelow = False
    # Milestone rows compute "◆" between bar rows computing "█": Excel's
    # inconsistent-formula checker stamps green triangles across the row
    # (Scott 2026-07-21, row-12 screenshot; the 2026-07-16 same-shape fix
    # helped only when milestones sat at a table edge). Declare the whole
    # day grid ignored for that check, the same mark Excel writes when a
    # user clicks "Ignore Error". openpyxl 3.1 has no serializer for it,
    # so _normalize_zip injects the element from this stash.
    wb._gp_ignored_errors = {ws.title: full_grid}
    return wb


def _serialize_wb(wb) -> bytes:
    # Determinism is a CLAIMED property (same-plan-same-bytes, asserted by
    # the acceptance test and relied on for artifact byte-stability), but
    # openpyxl stamps wall-clock time in two places: docProps/core.xml
    # created/modified, and every zip member's DOS mtime (2s resolution).
    # Renders straddling a second boundary produced different bytes —
    # a latent CI flake that struck twice on 2026-07-19. Freeze both.
    from datetime import datetime as _dt
    wb.properties.created = _dt(2026, 1, 1)
    wb.properties.modified = _dt(2026, 1, 1)
    # sheet title -> zip member name, for the ignored-errors injection
    ignored = {
        f"xl/worksheets/sheet{i + 1}.xml": sqref
        for i, title in enumerate(wb.sheetnames)
        for t, sqref in (getattr(wb, "_gp_ignored_errors", {}) or {}).items()
        if t == title
    }
    buf = BytesIO()
    wb.save(buf)
    return _normalize_zip(buf.getvalue(), ignored_errors=ignored)


def _slip_key(name: str) -> str:
    """Task identity across extraction runs: normalized name. Extraction
    won't spell a task identically every time ("Payments integration" vs
    "Payments Integration"), so match case/space/punctuation-blind and
    let anything that still misses fall out as "first tracked" rather
    than fabricate a lineage."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _compute_slip(tasks: list[dict], history: list[dict]) -> list[dict]:
    """Per-task due-date movement across dated plan versions.

    For each current non-phase task: walk history (oldest first, as-of
    ordered by the caller), collect its end date wherever the normalized
    name matches, append the current end, collapse consecutive equals.
    baseline = first tracked end; moves = number of changes; slip_days =
    current minus baseline. Tasks with no history rows are "first
    tracked" (baseline = current, zero moves) — honest, not padded."""
    out = []
    for t in tasks:
        if t.get("type") == "phase":
            continue
        key = _slip_key(t.get("name"))
        seq: list[tuple[str, date]] = []
        for ver in history or []:
            for ht in ver.get("tasks") or []:
                if ht.get("type") != "phase" and _slip_key(ht.get("name")) == key:
                    try:
                        seq.append((ver["as_of"], _d(ht["end"])))
                    except (KeyError, ValueError):
                        pass
                    break
        cur_end = _d(t["end"])
        if not seq or seq[-1][1] != cur_end:
            seq.append(("current", cur_end))
        changes = [seq[0]]
        for item in seq[1:]:
            if item[1] != changes[-1][1]:
                changes.append(item)
        out.append({
            "task": t, "baseline": seq[0][1], "baseline_as_of": seq[0][0],
            "current": cur_end, "moves": len(changes) - 1,
            "trail": changes, "first_tracked": len(seq) == 1,
        })
    return out


def render_gantt_detailed(data: dict, *, today: date | None = None,
                          history: list[dict] | None = None) -> bytes:
    """Detailed variant: the identical Gantt View sheet plus three additive
    sheets computed from the same extracted plan.

    The Gantt View itself carries % Done and Effort columns plus a live
    completed-portion bar overlay (Scott 2026-07-21: progress must be
    visible ON the timeline view, not exiled to a side sheet). Progress
    repeats percent and effort beside the receipts refs; both are
    STRICTLY blank when nobody said them (the extraction schema forbids
    estimating; an empty cell reads more honestly than an invented
    number). Workload
    is live COUNTIFS arithmetic over Progress (owner by week-due), so
    editing dates in Excel re-flags overloaded weeks. Slip (v2,
    2026-07-21) compares this plan's due dates against the project's
    prior snapshot versions (as-of ordered by meeting date): baseline,
    current, times moved, slip days. Receipts quotes the meeting line
    behind every extracted value; Progress rows cite [R#] refs into
    it."""
    from openpyxl.formatting.rule import CellIsRule, FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = _build_gantt_wb(data, today=today, detail_cols=True)
    tasks = data.get("tasks") or []
    rows = [t for t in tasks if t.get("type") != "phase"]

    navy, amber_lt, red_lt, gray_lt = "1F3A5F", "FBF0D5", "F6D3CE", "F2F3F5"
    hdr_font = Font(bold=True, color="FFFFFFFF", size=10)
    hdr_fill = PatternFill("solid", fgColor="FF" + navy)
    sub_font = Font(size=9, color="FF666666", italic=True)
    thin = Side(style="thin", color="FFD9D9D9")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ---- Receipts numbering: plan order, evidence order ----
    receipts: list[tuple[str, dict, dict]] = []   # (ref, task, evidence item)
    refs_of: dict[int, list[str]] = {}
    for t in rows:
        for ev in (t.get("evidence") or []):
            if not isinstance(ev, dict) or not str(ev.get("quote") or "").strip():
                continue
            ref = f"R{len(receipts) + 1}"
            receipts.append((ref, t, ev))
            refs_of.setdefault(t["id"], []).append(ref)

    # ---- Progress sheet ----
    ws = wb.create_sheet("Progress")
    ws["A1"] = "Progress, from what people actually said"
    ws["A1"].font = Font(bold=True, size=13, color="FF" + navy)
    ws["A2"] = ("Percent complete and effort appear only when someone "
                "stated them in a meeting; a blank cell means nobody said "
                "it. [R#] points to the verbatim line on the Receipts "
                "sheet.")
    ws["A2"].font = sub_font
    heads = ["Task", "Owner", "Status", "Due", "% Complete",
             "Effort (as stated)", "Receipts"]
    for ci, h in enumerate(heads, 1):
        c = ws.cell(4, ci, h)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
    for ri, t in enumerate(rows, 5):
        ws.cell(ri, 1, t["name"]).font = Font(bold=True, size=9)
        ws.cell(ri, 2, (t.get("owner") or "").strip())
        ws.cell(ri, 3, _STATUS_LABELS.get(t.get("status", ""), ""))
        dc = ws.cell(ri, 4, _d(t["end"]))
        dc.number_format = "yyyy-mm-dd"
        pct = t.get("percent_complete")
        if isinstance(pct, int) and 0 <= pct <= 100:
            pc = ws.cell(ri, 5, pct / 100)
            pc.number_format = "0%"
        eff = t.get("effort")
        if isinstance(eff, str) and eff.strip():
            ws.cell(ri, 6, eff.strip())
        refs = refs_of.get(t["id"])
        if refs:
            rc = ws.cell(ri, 7, ", ".join(refs))
            rc.font = Font(size=8, color="FF666666")
        for ci in range(1, 8):
            ws.cell(ri, ci).border = box
    last_progress_row = 4 + len(rows)
    for col, w in {"A": 34, "B": 14, "C": 12, "D": 12, "E": 11,
                   "F": 16, "G": 14}.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"

    # ---- Workload sheet (live COUNTIFS over Progress) ----
    wl = wb.create_sheet("Workload")
    wl["A1"] = "Workload, tasks due per person per week"
    wl["A1"].font = Font(bold=True, size=13, color="FF" + navy)
    wl["A2"] = ("Computed live from the Progress sheet, so edited dates "
                "re-flag automatically. 3 or more due in one week shows "
                "red, 2 shows amber. The gold header is the current week.")
    wl["A2"].font = sub_font
    start = min(_d(t["start"]) for t in tasks)
    end = max(_d(t["end"]) for t in tasks)
    w0 = start - timedelta(days=start.weekday())
    n_weeks = min(((end - w0).days // 7) + 1, 26)
    weeks = [w0 + timedelta(days=7 * i) for i in range(n_weeks)]
    wl.cell(3, 1, "Owner").font = hdr_font
    wl.cell(3, 1).fill = hdr_fill
    wl.cell(4, 1, "week ending").font = Font(size=8, color="FF888888")
    for wi, ws_start in enumerate(weeks):
        L = get_column_letter(2 + wi)
        cs = wl.cell(3, 2 + wi, ws_start)
        cs.number_format = "mmm d"
        cs.font = hdr_font
        cs.fill = hdr_fill
        cs.alignment = Alignment(horizontal="center")
        ce = wl.cell(4, 2 + wi, ws_start + timedelta(days=6))
        ce.number_format = "mmm d"
        ce.font = Font(size=8, color="FF888888")
        ce.alignment = Alignment(horizontal="center")
        wl.column_dimensions[L].width = 9
    owners = sorted({(t.get("owner") or "").strip()
                     for t in rows if (t.get("owner") or "").strip()})
    for oi, owner in enumerate(owners, 5):
        wl.cell(oi, 1, owner).font = Font(bold=True, size=9)
        for wi in range(n_weeks):
            L = get_column_letter(2 + wi)
            f = (f"=COUNTIFS(Progress!$B$5:$B${last_progress_row},$A{oi},"
                 f"Progress!$D$5:$D${last_progress_row},\">=\"&{L}$3,"
                 f"Progress!$D$5:$D${last_progress_row},\"<=\"&{L}$4)")
            c = wl.cell(oi, 2 + wi, f)
            c.alignment = Alignment(horizontal="center")
            c.border = box
    if owners:
        rng = (f"B5:{get_column_letter(1 + n_weeks)}{4 + len(owners)}")
        wl.conditional_formatting.add(rng, CellIsRule(
            operator="greaterThanOrEqual", formula=["3"],
            fill=PatternFill(start_color="FF" + red_lt,
                             end_color="FF" + red_lt, fill_type="solid"),
            font=Font(bold=True, color="FF9A1B12")))
        wl.conditional_formatting.add(rng, CellIsRule(
            operator="equal", formula=["2"],
            fill=PatternFill(start_color="FF" + amber_lt,
                             end_color="FF" + amber_lt, fill_type="solid")))
        # current-week header tracks TODAY() live, like the Gantt grid
        hdr_rng = f"B3:{get_column_letter(1 + n_weeks)}3"
        wl.conditional_formatting.add(hdr_rng, FormulaRule(
            formula=["AND(B$3<=TODAY(),TODAY()<=B$4)"],
            fill=PatternFill(start_color="FF8A6D1D", end_color="FF8A6D1D",
                             fill_type="solid")))
    wl.column_dimensions["A"].width = 16

    # ---- Slip sheet (v2: snapshot history) ----
    sl = wb.create_sheet("Slip")
    sl["A1"] = "Slip, how due dates moved across plan versions"
    sl["A1"].font = Font(bold=True, size=13, color="FF" + navy)
    sl["A2"] = ("Every generated plan is a dated version, so slip is "
                "computed, not remembered. Baseline is the due date in "
                "the earliest version that tracks the task; the trail "
                "shows each move with the meeting it came from.")
    sl["A2"].font = sub_font
    slip_heads = ["Task", "Owner", "Baseline due", "As of", "Current due",
                  "Times moved", "Slip (days)", "Trail"]
    for ci, h in enumerate(slip_heads, 1):
        c = sl.cell(4, ci, h)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
    slip_rows = _compute_slip(tasks, history)
    for ri, srow in enumerate(slip_rows, 5):
        t = srow["task"]
        sl.cell(ri, 1, t["name"]).font = Font(bold=True, size=9)
        sl.cell(ri, 2, (t.get("owner") or "").strip())
        bc = sl.cell(ri, 3, srow["baseline"])
        bc.number_format = "yyyy-mm-dd"
        sl.cell(ri, 4, "first tracked now" if srow["first_tracked"]
                else srow["baseline_as_of"]).font = Font(size=8,
                                                         color="FF666666")
        cc = sl.cell(ri, 5, srow["current"])
        cc.number_format = "yyyy-mm-dd"
        mv = sl.cell(ri, 6, srow["moves"])
        mv.alignment = Alignment(horizontal="center")
        sc = sl.cell(ri, 7, f"=E{ri}-C{ri}")
        sc.number_format = "0"
        sc.alignment = Alignment(horizontal="center")
        def _as_of(label: str) -> str:
            try:
                return datetime.strptime(label, "%Y-%m-%d").strftime("%b %d")
            except ValueError:
                return label
        trail = " → ".join(
            f"{d.strftime('%b %d')} (as of {_as_of(label)})"
            if label != "current" else f"{d.strftime('%b %d')} (current)"
            for label, d in srow["trail"])
        sl.cell(ri, 8, trail if srow["moves"] else "").font = Font(size=8)
        if srow["moves"] >= 2:
            for ci in range(1, 9):
                sl.cell(ri, ci).fill = PatternFill("solid",
                                                   fgColor="FF" + amber_lt)
            mv.font = Font(bold=True, color="FF9A3412")
        for ci in range(1, 9):
            sl.cell(ri, ci).border = box
    if not (history or []):
        note_r = 5 + len(slip_rows) + 1
        sl.cell(note_r, 1,
                "History starts with this version: every future gantt "
                "for this project adds a dated version to compare "
                "against.").font = sub_font
    for col, w in {"A": 30, "B": 14, "C": 12, "D": 15, "E": 12, "F": 11,
                   "G": 10, "H": 52}.items():
        sl.column_dimensions[col].width = w
    sl.freeze_panes = "A5"

    # ---- Receipts sheet ----
    rc = wb.create_sheet("Receipts")
    rc["A1"] = "Receipts, the meeting line behind every value"
    rc["A1"].font = Font(bold=True, size=13, color="FF" + navy)
    rc["A2"] = ("The plan is generated from the meetings, so provenance is "
                "automatic. Quotes are verbatim from the source content.")
    rc["A2"].font = sub_font
    for ci, h in enumerate(["Ref", "Task", "Supports", "Speaker",
                            "Verbatim line"], 1):
        c = rc.cell(4, ci, h)
        c.font = hdr_font
        c.fill = hdr_fill
    for ri, (ref, t, ev) in enumerate(receipts, 5):
        rc.cell(ri, 1, ref).font = Font(bold=True, size=9)
        rc.cell(ri, 2, t["name"]).font = Font(size=9)
        rc.cell(ri, 3, str(ev.get("field") or "")).font = Font(size=9)
        rc.cell(ri, 4, str(ev.get("speaker") or "")).font = Font(size=9)
        q = rc.cell(ri, 5, f'"{str(ev.get("quote")).strip()}"')
        q.font = Font(size=9)
        q.alignment = Alignment(wrap_text=True)
        for ci in range(1, 6):
            rc.cell(ri, ci).border = box
            if ri % 2 == 0:
                rc.cell(ri, ci).fill = PatternFill("solid",
                                                   fgColor="FF" + gray_lt)
    for col, w in {"A": 6, "B": 30, "C": 16, "D": 12, "E": 64}.items():
        rc.column_dimensions[col].width = w
    rc.freeze_panes = "A5"

    return _serialize_wb(wb)


# The user's style word maps to a concrete registry entry at arm time
# (offers store the FAMILY template; see the template lane in chat.py).
STYLE_TO_TEMPLATE = {"simple": "gantt_smartsheet", "detailed": "gantt_detailed"}


def _normalize_zip(blob: bytes,
                   ignored_errors: dict[str, str] | None = None) -> bytes:
    """Re-pack the xlsx with fixed member timestamps (content, order, and
    compression preserved) so identical content is identical bytes.

    ignored_errors maps zip member name -> A1 range: injects the
    <ignoredErrors> element (the mark Excel writes on "Ignore Error") so
    the inconsistent-formula checker stays quiet over the day grid.
    Inserted immediately before </worksheet>, after pageMargins, which is
    a schema-valid position for sheets without drawings/tables."""
    import zipfile
    src = zipfile.ZipFile(BytesIO(blob))
    out = BytesIO()
    import re as _re
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist():
            data = src.read(name)
            sqref = (ignored_errors or {}).get(name)
            if sqref:
                data = data.replace(
                    b"</worksheet>",
                    b'<ignoredErrors><ignoredError sqref="'
                    + sqref.encode() + b'" formula="1"/></ignoredErrors>'
                    b"</worksheet>")
            if name == "docProps/core.xml":
                # openpyxl overwrites dcterms:modified with wall-clock at
                # save time (setting wb.properties beforehand is futile) —
                # pin both stamps here instead.
                for tag in (b"created", b"modified"):
                    # keep the element's own attributes (openpyxl declares
                    # xmlns:xsi element-scoped) — pin only the text value
                    data = _re.sub(
                        b"(<dcterms:" + tag + b"[^>]*>)[^<]*(</dcterms:"
                        + tag + b">)",
                        lambda m: m.group(1) + b"2026-01-01T00:00:00Z"
                        + m.group(2),
                        data)
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return out.getvalue()


TEMPLATES = {
    "gantt_smartsheet": {
        "hints": ("gantt", "project timeline", "project plan chart",
                  "timeline chart", "diagrama de gantt", "ガントチャート"),
        "extraction_prompt": _GANTT_SCHEMA_PROMPT,
        "renderer": render_gantt,
        "format": "xlsx",
        "media_type": XLSX_MIME,
        "filename": "Gantt.xlsx",
        "expected_seconds": 45,  # measured 2026-07-12: 6s toy plan, 48s real 12-meeting project
        "offer_noun": "my polished Gantt chart (collapsible phases, status "
                      "colors, critical dates, a native Excel file)",
    },
    # Detailed variant (v1, 2026-07-21). hints EMPTY on purpose:
    # match_template never picks it directly — the family match is always
    # gantt_smartsheet, and the user's style choice (reply word or saved
    # per-project preference) swaps to this entry at arm time in chat.py.
    "gantt_detailed": {
        "hints": (),
        "extraction_prompt": _GANTT_DETAILED_SCHEMA_PROMPT,
        "renderer": render_gantt_detailed,
        "format": "xlsx",
        "media_type": XLSX_MIME,
        "filename": "Gantt_Detailed.xlsx",
        "expected_seconds": 60,  # richer extraction output than simple's 45
        "max_tokens": 12000,     # evidence quotes fatten the JSON
        "offer_noun": "my detailed Gantt workbook (the live timeline plus "
                      "percent complete as people stated it, per-person "
                      "workload flags, and a receipts sheet quoting the "
                      "meeting line behind every value)",
    },
}


def artifact_filename(template: dict, plan: dict) -> str:
    """Distinctive artifact name: <Project>_<Base>_<MMDDYY>.<ext>.
    Five identical Project_Gantt.xlsx rows in the client's References
    made artifacts indistinguishable (Scott 2026-07-14) — the project
    slug comes from the extracted plan, and the stamp is the MEETING
    date the extraction read from the content (Scott's call: the
    artifact describes the meeting, not the build; also keeps the name
    deterministic per plan). Falls back to the UTC build date when the
    plan carries no parsable date. No project -> <Base>_<MMDDYY>.<ext>."""
    base, ext = template["filename"].rsplit(".", 1)
    slug = re.sub(r"[^A-Za-z0-9]+", "_",
                  str(plan.get("project") or "")).strip("_")[:40]
    try:
        stamp = datetime.strptime(
            str(plan.get("meeting_date")), "%Y-%m-%d").strftime("%m%d%y")
    except ValueError:
        stamp = datetime.now(timezone.utc).strftime("%m%d%y")
    return "_".join(p for p in (slug, base, stamp) if p) + "." + ext


def match_template(text: str, format: str | None = None) -> str | None:
    """Scan the WHOLE given text (capped), not a tail window: the first
    live miss was a 400-word library prompt that said "Gantt" once, in its
    opening sentence — a tail slice cut the keyword out of its own prompt.
    Callers pass the full assembled content DELIBERATELY, unlike the
    intent checks (#420): anaphoric asks ("make IT into an excel doc")
    carry the template keyword only in history, and that case is live-
    proven wanted (2026-07-13 16:52 offer).

    `format` is the classifier's read of the DESIRED output ("docx"...):
    a template that builds a different format is vetoed. This is what
    contains the history scan's false-positive class — live 2026-07-14
    21:58Z, a Word roles-doc ask drew the xlsx Gantt offer off 'gantt'
    in carried history; the veto blocks it while anaphora keeps working."""
    hay = (text or "")[-50000:].lower()
    for tid, t in TEMPLATES.items():
        if format and t["format"] != format:
            continue
        if any(h in hay for h in t["hints"]):
            return tid
    return None


def parse_extraction(text: str) -> dict:
    """JSON recovery from the extraction turn. Models sometimes narrate
    around the object or append a rendering despite the output-only-JSON
    instruction (live 2026-07-13 19:16Z: prose + valid plan JSON + a full
    HTML page — the old first-{-to-last-} slice ended inside the HTML's
    CSS braces and failed on a turn that carried a perfectly good plan).
    Decode balanced objects wherever they start and prefer the one that
    looks like a plan; fall back to the largest object found."""
    t = text or ""
    dec = json.JSONDecoder()
    candidates: list[dict] = []
    i = t.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(t, i)
            if isinstance(obj, dict):
                if "tasks" in obj:
                    return obj
                candidates.append(obj)
                i = t.find("{", end)
                continue
        except json.JSONDecodeError:
            pass
        i = t.find("{", i + 1)
    if candidates:
        return max(candidates, key=lambda o: len(json.dumps(o)))
    raise ValueError("no JSON object in extraction text")
