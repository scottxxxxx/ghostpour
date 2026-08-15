"""Column contracts for meeting artifacts we know the shape of.

Measured 2026-08-14: when the model designs its own columns, it designs
DIFFERENT ones every run. Across three identical runs, Sonnet 5 included
an expected-result column once, and Opus 4.8 twice. A test scenario with
no expected result is not testable, so those workbooks look full and are
unusable, and nothing in the output signals it.

Asking harder does not fix that; the coverage push was already in the
prompt for every one of those runs. So for artifact types we recognise,
we stop asking. The columns are ours, the rows are the model's. The
contract becomes the tool's input schema, which means a missing
`expected` is a schema violation the API layer rejects and the model
retries, rather than a defect that ships.

This also makes a new artifact type a config entry rather than new code:
add a Contract here and the tool schema, the renderer and the house
style all come along.
"""

from __future__ import annotations

from typing import Any

from app.services.workbook_plan import WORKBOOK_PLAN_SCHEMA


class Column:
    """One column of a contract.

    `description` is not documentation, it is the prompt: it is what the
    model reads when deciding what belongs in this cell.
    """

    def __init__(self, key: str, label: str, description: str,
                 width: int = 30, wrap: bool = True,
                 fmt: str = "text", required: bool = False,
                 json_type: str = "string",
                 computed: dict[str, Any] | None = None) -> None:
        self.key = key
        self.label = label
        self.description = description
        self.width = width
        self.wrap = wrap
        self.fmt = fmt
        self.required = required
        # A numeric field must arrive as a number, or Excel sorts and
        # sums it as text and the formulas silently return zero.
        self.json_type = json_type
        # Declared intent, resolved to a formula by the renderer. A
        # computed column is never asked of the model.
        self.computed = computed

    def as_plan_column(self) -> dict[str, Any]:
        col: dict[str, Any] = {
            "key": self.key, "label": self.label, "width": self.width,
            "wrap": self.wrap, "format": self.fmt}
        if self.computed:
            col["computed"] = self.computed
        return col


class Contract:
    def __init__(self, name: str, label: str, sheet_rule: str,
                 columns: list[Column], filename_hint: str,
                 axis: str | None = None,
                 totals: list[str] | None = None,
                 repair: Any = None) -> None:
        # Cross-field consistency the schema cannot express. Called per
        # row, returns the corrected row. Repairs are counted, not
        # silent: a contract that needs many of them is mis-worded.
        self.repair = repair
        self.name = name
        self.label = label
        self.sheet_rule = sheet_rule
        self.columns = columns
        self.filename_hint = filename_hint
        # Some artifacts have a second axis the model must name: the
        # options being compared. Those become extra columns whose
        # HEADERS are the model's and whose position is ours.
        self.axis = axis
        # Column keys that get a live SUM in a totals row.
        self.totals = totals or []

    @property
    def required(self) -> list[str]:
        return [c.key for c in self.columns
                if c.required and not c.computed]

    @property
    def model_columns(self) -> list[Column]:
        """Columns the model fills. A computed column is not one."""
        return [c for c in self.columns if not c.computed]


TEST_PLAN = Contract(
    name="test_plan",
    label="Test scenario plan",
    sheet_rule=(
        "One sheet per intent, feature area, or capability under test. "
        "Name the sheet after that area alone. Do not create a summary or "
        "index sheet that repeats rows from the other sheets."),
    filename_hint="test_scenarios",
    columns=[
        Column("id", "Scenario ID",
               "Short stable ID, a per-sheet prefix plus a number, for "
               "example RFL-01. Unique across the whole workbook.",
               width=12, wrap=False, required=True),
        Column("scenario", "Scenario",
               "What is being tested, as a short phrase a person can scan. "
               "Not a sentence.",
               width=34, required=True),
        Column("type", "Type",
               "Exactly one of: Happy path, Edge case, Failure mode, "
               "Boundary, Data quality.",
               width=14, required=True),
        Column("preconditions", "Preconditions",
               "The state the system and the account must be in before "
               "this runs. Say 'None' if there genuinely are none.",
               width=30),
        Column("test_data", "Sample Test Data",
               "Concrete values a developer can paste in and run: real "
               "looking IDs, names, dates, amounts, plan or product "
               "names. Never a placeholder like MEM-001 or TBD. Every "
               "scenario gets its own data, not a shared example.",
               width=44, required=True),
        Column("expected", "Expected Behavior",
               "What the system must do, specifically enough that two "
               "engineers would agree whether it passed. Name the exact "
               "values or messages returned where you can.",
               width=44, required=True),
        Column("notes", "Notes for Dev",
               "Domain explanation the dev team would not already know, "
               "why this case matters, or what it is easy to get wrong. "
               "Leave empty when there is nothing worth saying.",
               width=34),
    ],
)

_SOURCE = Column(
    "source", "Said In",
    "Which meeting and roughly when this came from, so a reader can go "
    "back to it. Use the meeting name and date you were given.",
    width=24)

ACTION_REGISTER = Contract(
    name="action_register",
    label="Action items and commitments",
    sheet_rule=(
        "One sheet named 'Actions'. Add a second sheet per workstream "
        "only if there are more than about twenty five items."),
    filename_hint="action_items",
    columns=[
        Column("id", "ID", "Short ID such as A-01.", width=8,
               wrap=False, required=True),
        Column("item", "Action",
               "The commitment as a single thing someone will do, "
               "starting with a verb. Not a topic that was discussed.",
               width=40, required=True),
        Column("owner", "Owner",
               "The person who took it, by name as spoken. Write "
               "'Unassigned' when nobody actually accepted it, and do "
               "NOT guess an owner from who happened to raise it.",
               width=18, required=True),
        Column("due", "Due",
               "Date as YYYY-MM-DD if one was stated or clearly implied. "
               "Leave empty when no date was given. Never invent one.",
               width=12, wrap=False, fmt="date"),
        Column("status", "Status",
               "Exactly one of: Not started, In progress, Blocked, Done.",
               width=13, required=True),
        Column("blocker", "Blocked By",
               "What has to happen first, or who they are waiting on. "
               "Empty when nothing blocks it.",
               width=28),
        _SOURCE,
        Column("notes", "Notes",
               "Context that changes how it should be done. Empty when "
               "there is nothing worth saying.", width=30),
    ],
)

DECISION_LOG = Contract(
    name="decision_log",
    label="Decision log",
    sheet_rule="One sheet named 'Decisions'.",
    filename_hint="decisions",
    columns=[
        Column("id", "ID", "Short ID such as D-01.", width=8,
               wrap=False, required=True),
        Column("decision", "Decision",
               "What was settled, stated as the resolution and not as "
               "the debate. A reader who missed the meeting should be "
               "able to act on this sentence alone.",
               width=42, required=True),
        Column("date", "Date", "YYYY-MM-DD of the meeting it was taken in.",
               width=12, wrap=False, fmt="date"),
        Column("decided_by", "Decided By",
               "Who actually made the call, by name. If the room agreed "
               "without one person deciding, write 'Group'.",
               width=18, required=True),
        Column("rationale", "Why",
               "The reason given IN the meeting. Do not supply a better "
               "reason than the one that was actually said.",
               width=42, required=True),
        Column("alternatives", "Rejected",
               "What was considered and set aside, and why. Empty if "
               "nothing else was genuinely on the table.",
               width=34),
        Column("reversible", "Reversible",
               "One of: Easily, With effort, Hard to undo. Judge from "
               "what the decision commits: a tool choice is not a "
               "hiring decision.",
               width=14),
        Column("affects", "Affects",
               "Who or what changes because of this.", width=26),
        _SOURCE,
    ],
)

RISK_REGISTER = Contract(
    name="risk_register",
    label="Risk register",
    sheet_rule="One sheet named 'Risks'.",
    filename_hint="risks",
    columns=[
        Column("id", "ID", "Short ID such as R-01.", width=8,
               wrap=False, required=True),
        Column("risk", "Risk",
               "Phrase it as the thing that could go wrong and its "
               "consequence, not as a general worry. 'X happens, so Y'.",
               width=44, required=True),
        Column("category", "Category",
               "One of: Technical, Schedule, Resourcing, External, "
               "Scope, Compliance.", width=14),
        Column("likelihood", "Likelihood",
               "Integer 1 to 5, where 1 is remote and 5 is expected. "
               "Base it on what was said, not on a default of 3.",
               width=11, wrap=False, fmt="number", json_type="integer",
               required=True),
        Column("impact", "Impact",
               "Integer 1 to 5, where 1 is a nuisance and 5 derails the "
               "project.",
               width=9, wrap=False, fmt="number", json_type="integer",
               required=True),
        Column("severity", "Severity",
               "Computed, not yours.", width=10, wrap=False, fmt="number",
               computed={"op": "product", "of": ["likelihood", "impact"]}),
        Column("owner", "Owner",
               "Who is watching it. 'Unassigned' if nobody took it.",
               width=16, required=True),
        Column("mitigation", "Mitigation",
               "What reduces the likelihood or the impact, specifically "
               "enough to act on.", width=40, required=True),
        Column("trigger", "Early Warning",
               "The observable sign this is turning real, so someone "
               "knows when to act.", width=32),
        _SOURCE,
    ],
)

OPEN_QUESTIONS = Contract(
    name="open_questions",
    label="Open questions and blockers",
    sheet_rule="One sheet named 'Open Questions'.",
    filename_hint="open_questions",
    columns=[
        Column("id", "ID", "Short ID such as Q-01.", width=8,
               wrap=False, required=True),
        Column("question", "Question",
               "The thing that is genuinely unresolved, as a question. "
               "Not a task, and not something that was answered later in "
               "the same meeting.", width=44, required=True),
        Column("raised_by", "Raised By", "Who asked it, by name.",
               width=16, required=True),
        Column("blocks", "Blocks",
               "What cannot proceed until this is answered. Empty when "
               "it is merely open rather than blocking.", width=30),
        Column("owner", "Who Can Answer",
               "The person or team who can settle it. 'Unknown' if the "
               "meeting did not identify anyone.", width=20,
               required=True),
        Column("needed_by", "Needed By",
               "YYYY-MM-DD if a date was stated. Never invent one.",
               width=12, wrap=False, fmt="date"),
        Column("status", "Status",
               "One of: Open, Answered, Escalated, Parked.", width=12,
               required=True),
        _SOURCE,
    ],
)

REQUIREMENTS = Contract(
    name="requirements",
    label="Requirements and scope",
    sheet_rule=(
        "One sheet per capability or feature area. Name the sheet after "
        "that area."),
    filename_hint="requirements",
    columns=[
        Column("id", "ID", "Short ID such as REQ-01.", width=9,
               wrap=False, required=True),
        Column("requirement", "Requirement",
               "One testable statement of what the system must do. If it "
               "contains 'and', consider splitting it into two rows.",
               width=44, required=True),
        Column("priority", "Priority",
               "Exactly one of: Must, Should, Could, Will not. Use the "
               "priority the meeting assigned; only judge for yourself "
               "when none was given.", width=12, required=True),
        Column("acceptance", "Acceptance Criteria",
               "How anyone would demonstrate this is met. Concrete "
               "enough that two engineers would agree it passed.",
               width=44, required=True),
        Column("source_quote", "Heard As",
               "A short near-verbatim line from the meeting this came "
               "from, so the requirement is traceable to something "
               "somebody actually said.", width=40),
        Column("owner", "Owner", "Who owns delivering it.", width=16),
        Column("notes", "Notes", "Open issues or dependencies.",
               width=28),
    ],
)

OPTION_COMPARISON = Contract(
    name="option_comparison",
    label="Option comparison",
    sheet_rule="One sheet named 'Comparison'.",
    filename_hint="comparison",
    axis=("The options being compared, in the order they should appear "
          "as columns. Use the names the meeting used."),
    columns=[
        Column("criterion", "Criterion",
               "What is being compared on, phrased so that better is "
               "unambiguous. One row per criterion.",
               width=30, required=True),
        Column("weight", "Weight",
               "How much this criterion matters, 1 to 5. Use 3 only when "
               "the meeting genuinely treated it as middling.",
               width=8, wrap=False, fmt="number", json_type="integer"),
        Column("notes", "Notes",
               "What the meeting actually said about this criterion.",
               width=34),
    ],
)

def _budget_repair(row: dict) -> dict:
    """A provenance claim with nothing to back it is downgraded.

    Live 2026-08-15 against a real SOW overrun meeting: 4 of 13 rows came
    back confidence "Stated" with both amounts empty, because the model
    read "Stated" as "this line item was discussed" rather than "a number
    was said out loud". We cannot verify that a figure was spoken, but we
    CAN verify there is no figure here, and "Stated" next to an empty
    cell is the exact reading that makes a budget untrustworthy.
    """
    has_amount = (row.get("low") is not None or row.get("high") is not None)
    if not has_amount and row.get("confidence") in ("Stated", "Estimated"):
        row = dict(row)
        row["confidence"] = "Not quantified"
    return row


BUDGET = Contract(
    name="budget",
    repair=_budget_repair,
    label="Budget and cost estimate",
    sheet_rule="One sheet named 'Estimate'.",
    filename_hint="budget",
    totals=["low", "high"],
    columns=[
        Column("id", "ID", "Short ID such as B-01.", width=8,
               wrap=False, required=True),
        Column("line_item", "Line Item",
               "The thing being paid for.", width=34, required=True),
        Column("category", "Category",
               "One of: People, Software, Infrastructure, Services, "
               "Hardware, Other.", width=15),
        Column("basis", "Basis",
               "Where the number comes from. If the meeting stated a "
               "figure, say so and quote it. If nobody gave a number, "
               "write 'Not stated in meeting' and leave the amounts "
               "EMPTY. An invented figure is worse than a gap, because "
               "it will be read as a quote.",
               width=44, required=True),
        Column("low", "Low",
               "Low estimate in dollars, as a number with no symbols. "
               "Leave empty unless the meeting supports a figure.",
               width=12, wrap=False, fmt="currency", json_type="number"),
        Column("high", "High",
               "High estimate in dollars, as a number with no symbols. "
               "Leave empty unless the meeting supports a figure.",
               width=12, wrap=False, fmt="currency", json_type="number"),
        Column("spread", "Spread",
               "Computed, not yours.", width=12, wrap=False,
               fmt="currency",
               computed={"op": "difference", "of": ["high", "low"]}),
        Column("confidence", "Confidence",
               "One of: Stated, Estimated, Guess, Not quantified. Use "
               "'Stated' ONLY when a figure was actually said out loud, "
               "and never on a row where you left both amounts empty; "
               "that row is 'Not quantified'.", width=15,
               required=True),
        Column("owner", "Owner", "Who owns this line.", width=16),
    ],
)

TOPIC_TRACKER = Contract(
    name="topic_tracker",
    label="Cross-meeting topic tracker",
    sheet_rule="One sheet named 'Topics'.",
    filename_hint="topic_tracker",
    columns=[
        Column("topic", "Topic",
               "The recurring thread, named the way the team refers to "
               "it. One row per topic, not per mention.",
               width=34, required=True),
        Column("first_raised", "First Raised",
               "YYYY-MM-DD of the earliest meeting in your context that "
               "discussed it.", width=13, wrap=False, fmt="date",
               required=True),
        Column("last_discussed", "Last Discussed",
               "YYYY-MM-DD of the most recent one.", width=14,
               wrap=False, fmt="date", required=True),
        Column("times_discussed", "Times Up",
               "How many distinct meetings in your context touched it. "
               "Count meetings, not sentences.",
               width=10, wrap=False, fmt="number", json_type="integer",
               required=True),
        Column("status", "Where It Stands",
               "The current state in one line, as of the latest meeting.",
               width=40, required=True),
        Column("movement", "Movement",
               "One of: Progressing, Stalled, Reopened, Resolved, "
               "Escalating. 'Reopened' when it was settled and came "
               "back, which is the pattern most worth surfacing.",
               width=14, required=True),
        Column("owner", "Owner",
               "Who carries it. 'Unassigned' if it keeps coming up with "
               "nobody owning it, which is usually why it recurs.",
               width=16),
        Column("open_question", "Still Unresolved",
               "What is still not settled. Empty when the topic is "
               "genuinely closed.", width=38),
    ],
)

CONTRACTS: dict[str, Contract] = {c.name: c for c in (
    TEST_PLAN, ACTION_REGISTER, DECISION_LOG, RISK_REGISTER,
    OPEN_QUESTIONS, REQUIREMENTS, OPTION_COMPARISON, BUDGET,
    TOPIC_TRACKER,
)}


def contract_tool_schema(contract: Contract) -> dict[str, Any]:
    """The tool input schema for a contracted artifact.

    Same envelope as the freeform plan (filename, title, readme,
    needs_computation) but the model supplies only sheet names and rows.
    Columns are not up for negotiation, and `additionalProperties: false`
    stops the model smuggling in an extra field the renderer would drop
    on the floor.
    """
    base = WORKBOOK_PLAN_SCHEMA["properties"]
    row_props: dict[str, Any] = {}
    for c in contract.model_columns:
        spec: dict[str, Any] = {"description": c.description}
        # Optional fields must tolerate an explicit null, or the model
        # pads them with "N/A" to satisfy the type.
        spec["type"] = (c.json_type if c.required
                        else [c.json_type, "null"])
        row_props[c.key] = spec
    if contract.axis:
        row_props["values"] = {
            "type": "array",
            "items": {"type": ["string", "number", "null"]},
            "description": (
                "One entry per option in `options`, in the same order. "
                "Same length as `options`, every time."),
        }

    sheet_props: dict[str, Any] = {
        "name": {"type": "string", "description": contract.sheet_rule},
        "subtitle": {"type": "string",
                     "description": "One line under the tab title."},
        "rows": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": (contract.required
                             + (["values"] if contract.axis else [])),
                "additionalProperties": False,
                "properties": row_props,
            },
        },
    }

    props: dict[str, Any] = {
        "filename": {
            "type": "string",
            "description": (f"Lowercase, underscores, including "
                            f"'{contract.filename_hint}', ending in .xlsx."),
        },
        "title": base["title"],
        "needs_computation": base["needs_computation"],
        "readme": base["readme"],
        "sheets": {
            "type": "array", "minItems": 1,
            "items": {"type": "object", "required": ["name", "rows"],
                      "properties": sheet_props},
        },
    }
    if contract.axis:
        props["options"] = {
            "type": "array", "minItems": 2,
            "items": {"type": "string"},
            "description": contract.axis,
        }
    return {
        "type": "object",
        "required": (["filename", "sheets"]
                     + (["options"] if contract.axis else [])),
        "properties": props,
    }


def plan_from_contract(contract: Contract, emitted: dict) -> dict:
    """Turn a contracted emission into the plan the renderer already takes.

    The model never sees column definitions, so we put them back. For a
    comparison, the option names it chose become extra columns and its
    per-row `values` array is spread across them positionally, so a short
    or long array cannot shift a score under the wrong option.
    """
    cols = [c.as_plan_column() for c in contract.columns]
    options = [str(o) for o in (emitted.get("options") or [])
               ] if contract.axis else []
    for i, opt in enumerate(options):
        cols.append({"key": f"_opt{i}", "label": opt, "width": 20,
                     "wrap": True, "format": "text"})

    sheets = []
    for s in (emitted.get("sheets") or []):
        rows = []
        for raw in (s.get("rows") or []):
            if contract.repair:
                raw = contract.repair(raw)
            row = {k: v for k, v in raw.items() if k != "values"}
            for i in range(len(options)):
                vals = raw.get("values") or []
                row[f"_opt{i}"] = vals[i] if i < len(vals) else None
            rows.append(row)
        sheets.append({
            "name": s.get("name"), "subtitle": s.get("subtitle"),
            "orientation": "matrix" if contract.axis else "records",
            "columns": cols, "rows": rows,
            "totals": contract.totals,
        })

    return {
        "filename": (emitted.get("filename")
                     or f"{contract.filename_hint}.xlsx"),
        "title": emitted.get("title") or contract.label,
        "needs_computation": emitted.get("needs_computation", False),
        "readme": emitted.get("readme"),
        "sheets": sheets,
    }
