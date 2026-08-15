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
                 fmt: str = "text", required: bool = False) -> None:
        self.key = key
        self.label = label
        self.description = description
        self.width = width
        self.wrap = wrap
        self.fmt = fmt
        self.required = required

    def as_plan_column(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "width": self.width,
                "wrap": self.wrap, "format": self.fmt}


class Contract:
    def __init__(self, name: str, label: str, sheet_rule: str,
                 columns: list[Column], filename_hint: str) -> None:
        self.name = name
        self.label = label
        self.sheet_rule = sheet_rule
        self.columns = columns
        self.filename_hint = filename_hint

    @property
    def required(self) -> list[str]:
        return [c.key for c in self.columns if c.required]


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

CONTRACTS: dict[str, Contract] = {c.name: c for c in (TEST_PLAN,)}


def contract_tool_schema(contract: Contract) -> dict[str, Any]:
    """The tool input schema for a contracted artifact.

    Same envelope as the freeform plan (filename, title, readme,
    needs_computation) but the model supplies only sheet names and rows.
    Columns are not up for negotiation, and `additionalProperties: false`
    stops the model smuggling in an extra field the renderer would drop
    on the floor.
    """
    base = WORKBOOK_PLAN_SCHEMA["properties"]
    return {
        "type": "object",
        "required": ["filename", "sheets"],
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    f"Lowercase, underscores, including "
                    f"'{contract.filename_hint}', ending in .xlsx."),
            },
            "title": base["title"],
            "needs_computation": base["needs_computation"],
            "readme": base["readme"],
            "sheets": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["name", "rows"],
                    "properties": {
                        "name": {"type": "string",
                                 "description": contract.sheet_rule},
                        "subtitle": {
                            "type": "string",
                            "description": "One line under the tab title.",
                        },
                        "rows": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "required": contract.required,
                                "additionalProperties": False,
                                "properties": {
                                    c.key: {"type": "string",
                                            "description": c.description}
                                    for c in contract.columns
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def plan_from_contract(contract: Contract, emitted: dict) -> dict:
    """Turn a contracted emission into the plan the renderer already takes.

    The model never sees column definitions, so we put them back.
    """
    cols = [c.as_plan_column() for c in contract.columns]
    return {
        "filename": emitted.get("filename") or f"{contract.filename_hint}.xlsx",
        "title": emitted.get("title") or contract.label,
        "needs_computation": emitted.get("needs_computation", False),
        "readme": emitted.get("readme"),
        "sheets": [
            {"name": s.get("name"), "subtitle": s.get("subtitle"),
             "orientation": "records", "columns": cols,
             "rows": s.get("rows") or []}
            for s in (emitted.get("sheets") or [])
        ],
    }
