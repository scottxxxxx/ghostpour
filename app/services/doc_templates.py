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
from datetime import date, datetime, timedelta
from io import BytesIO

logger = logging.getLogger("ghostpour.doc_templates")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_GANTT_SCHEMA_PROMPT = (
    "Extract this project's plan from the conversation and meeting content "
    "as JSON ONLY — no prose, no code fences. Schema: {\"project\": str, "
    "\"tasks\": [{\"id\": int, \"name\": str, \"type\": \"phase\"|\"task\"|"
    "\"milestone\", \"parent_id\": int|null, \"owner\": str|null, "
    "\"status\": \"complete\"|\"in_progress\"|\"on_hold\"|\"not_started\"|"
    "\"blocked\", \"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\", "
    "\"depends_on\": [int]}]}. Rules: phases have parent_id null; tasks and "
    "milestones carry the id of their phase; milestones have start equal to "
    "end; dates must be consistent with dependencies (a task never starts "
    "before its predecessor ends); owner is the person's name as spoken. "
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


def render_gantt(data: dict, *, today: date | None = None) -> bytes:
    """Deterministic Smartsheet-style Gantt from extracted plan JSON."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    def fill(hex6):
        return PatternFill("solid", fgColor="FF" + hex6)

    tasks = data.get("tasks") or []
    if not tasks:
        raise ValueError("no tasks extracted")
    today = today or date.today()
    start = min(_d(t["start"]) for t in tasks)
    end = max(_d(t["end"]) for t in tasks)
    days = [(start + timedelta(n)) for n in range((end - start).days + 1)][:180]

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
    FIRST_DAY_COL = 8  # A:dot B:name C:risk D:start E:end F:chip G:owner name

    # two-row timeline header: week-of labels over Mondays + day letters
    for i, d in enumerate(days):
        col = FIRST_DAY_COL + i
        ws.column_dimensions[get_column_letter(col)].width = 3
        if d.weekday() == 0:
            c = ws.cell(1, col, f"Week of {d.strftime('%b %d')}")
            c.font = Font(size=8, bold=True)
        letter = "MTWTFSS"[d.weekday()]
        c = ws.cell(2, col, letter)
        c.alignment = Alignment(horizontal="center")
        c.font = Font(size=8, bold=True,
                      color="FF" + (_C["risk"] if d.weekday() >= 5 else "3D4653"))
        if d == today:
            c.font = Font(size=8, bold=True, color="FF" + _C["risk"])
    for col, (head, width) in enumerate(
            [("Status", 7), ("Task Name", 38), ("At\nRisk", 5),
             ("Start\nDate", 11), ("End\nDate", 11), ("", 4),
             ("Assigned To", 16)], start=1):
        c = ws.cell(2, col, head)
        c.font = Font(bold=True, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.fill = fill("E9E9E9")   # header band (Scott: "lost the cool highlighting")
        ws.column_dimensions[get_column_letter(col)].width = width

    # status key block — amber header band like the reference
    row = 3
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

    def timeline(r, t, bar_hex, label_hex, slim=False):
        s, e = _d(t["start"]), _d(t["end"])
        end_col = None
        for i, d in enumerate(days):
            col = FIRST_DAY_COL + i
            cell = ws.cell(r, col)
            if d.weekday() >= 5:
                cell.fill = fill(_C["weekend"])
            if d == today:
                cell.fill = fill(_C["today"])
            if s <= d <= e:
                cell.fill = fill(bar_hex)
                end_col = col
        if t.get("type") == "milestone" and end_col:
            m = ws.cell(r, end_col, "◆")
            m.font = Font(color="FF" + _C["risk"], bold=True)
            m.alignment = Alignment(horizontal="center")
        elif end_col and not slim and end_col + 1 <= FIRST_DAY_COL + len(days) - 1:
            lab = ws.cell(r, end_col + 1, " " + t["name"][:40])
            lab.font = Font(size=8, color="FF" + label_hex)
        if slim:
            ws.row_dimensions[r].height = 12

    # project row
    proj = {"name": data.get("project") or "Project",
            "start": str(start), "end": str(end), "type": "summary"}
    ws.cell(row, 2, f"  {proj['name']}").font = Font(bold=True, color="FFFFFFFF")
    for c in range(1, FIRST_DAY_COL):
        ws.cell(row, c).fill = fill(_C["project"])
    ws.cell(row, 4, str(start)); ws.cell(row, 5, str(end))
    timeline(row, proj, _C["summary"], _C["summary"], slim=True)
    row += 1

    chip_cache: dict = {}
    for phase in phases:
        kids = children.get(phase["id"], [])
        ws.cell(row, 1, "●").font = Font(color="FF" + _C["status"].get(phase.get("status", "in_progress"), _C["bar"]))
        ws.cell(row, 2, f"  −  {phase['name']}").font = Font(bold=True, size=9)
        ws.cell(row, 4, phase["start"]); ws.cell(row, 5, phase["end"])
        ws.row_dimensions[row].outline_level = 1
        timeline(row, phase, _C["summary"], _C["summary"], slim=True)
        row += 1
        for t in kids:
            risky = at_risk(t)
            text_hex = _C["risk"] if risky else "3D4653"
            ws.cell(row, 1, "●" if t["type"] != "milestone" else "").font = \
                Font(color="FF" + _C["status"].get(t["status"], _C["bar"]))
            name = ("          🏁 " if t["type"] == "milestone" else "          ") + t["name"]
            ws.cell(row, 2, name).font = Font(size=9, color="FF" + text_hex)
            fl = ws.cell(row, 3, "⚑" if risky else "⚐")
            fl.font = Font(color="FF" + (_C["risk"] if risky else "9AA4AF"))
            fl.alignment = Alignment(horizontal="center")
            ws.cell(row, 4, t["start"]).font = Font(size=8, color="FF" + text_hex)
            ws.cell(row, 5, t["end"]).font = Font(size=8, color="FF" + text_hex)
            owner = (t.get("owner") or "").strip()
            if owner:
                # chip = colored initials; FULL NAME beside it (Scott's
                # review: "I don't get the full name, just the letter")
                initials = "".join(w[0] for w in owner.split()[:2]).upper()
                hex_c = chip_cache.setdefault(
                    owner, _C["chips"][int(hashlib.sha256(owner.encode()).hexdigest(), 16) % len(_C["chips"])])
                chip = ws.cell(row, 6, initials)
                chip.fill = fill(hex_c)
                chip.font = Font(bold=True, size=8, color="FFFFFFFF")
                chip.alignment = Alignment(horizontal="center")
                nm = ws.cell(row, 7, owner)
                nm.font = Font(size=8, color="FF" + text_hex)
            ws.row_dimensions[row].outline_level = 2
            timeline(row, t, _C["risk"] if risky else _C["bar"], text_hex)
            # ↳ handoff glyph: predecessor ends the day this task starts
            for dep in t.get("depends_on") or []:
                pred = by_id.get(dep)
                if pred and abs((_d(t["start"]) - _d(pred["end"])).days) <= 1:
                    gap = FIRST_DAY_COL + (_d(t["start"]) - start).days
                    if ws.cell(row, gap).value is None:
                        g = ws.cell(row, gap, "↳")
                        g.font = Font(size=8, color="FF" + _C["summary"])
                    break
            row += 1

    ws.freeze_panes = "H3"
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
        "filename": "Project_Gantt.xlsx",
        "expected_seconds": 45,  # measured 2026-07-12: 6s toy plan, 48s real 12-meeting project
        "offer_noun": "my polished Gantt chart (collapsible phases, status "
                      "colors, critical dates — a native Excel file)",
    },
}


def match_template(text: str) -> str | None:
    """Scan the WHOLE ask (capped), not a tail window: the first live miss
    was a 400-word library prompt that said "Gantt" once, in its opening
    sentence — a tail slice cut the keyword out of its own prompt. A plain
    substring scan over 50K chars is microseconds; there's no reason to
    window it."""
    hay = (text or "")[-50000:].lower()
    for tid, t in TEMPLATES.items():
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
