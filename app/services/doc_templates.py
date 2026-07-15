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


def render_gantt(data: dict, *, today: date | None = None) -> bytes:
    """Deterministic Smartsheet-style Gantt from extracted plan JSON.

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
    FIRST_DAY_COL = 10
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
    for col, (head, width) in enumerate(
            [("", 7), ("Task Name", 38), ("At\nRisk", 5), ("Status", 12),
             ("Start\nDate", 11), ("End\nDate", 11), ("Predecessors", 12),
             ("", 4), ("Assigned To", 16)], start=1):
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

    def bar_rules(r, bar_hex, risk_aware=False):
        """The live bars: in-range fill; risk-aware rows get a red rule
        first (blocked, or not started past its start, judged LIVE via
        TODAY())."""
        E, F = f"$E{r}", f"$F{r}"
        ax = f"{get_column_letter(FIRST_DAY_COL)}$1"
        in_range = f"AND({ax}>={E},{ax}<={F})"
        if risk_aware:
            risky = (f"AND({ax}>={E},{ax}<={F},OR($D{r}=\"Blocked\","
                     f"AND($D{r}=\"Not Started\",{E}<TODAY())))")
            ws.conditional_formatting.add(grid(r), FormulaRule(
                formula=[risky], fill=dxf_fill(_C["risk"]), stopIfTrue=True))
        ws.conditional_formatting.add(grid(r), FormulaRule(
            formula=[in_range], fill=dxf_fill(bar_hex), stopIfTrue=True))

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
            ws.row_dimensions[row].outline_level = 2
            if t["type"] == "milestone":
                # the ◆ marker is a formula so it moves with the date
                for i in range(len(days)):
                    col = FIRST_DAY_COL + i
                    L = get_column_letter(col)
                    m = ws.cell(row, col, f'=IF({L}$1=$F{row},"◆","")')
                    m.font = Font(color="FF" + _C["risk"], bold=True)
                    m.alignment = Alignment(horizontal="center")
            else:
                bar_rules(row, _C["bar"], risk_aware=True)
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

    ws.freeze_panes = "J4"
    ws.sheet_properties.outlinePr.summaryBelow = False
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
