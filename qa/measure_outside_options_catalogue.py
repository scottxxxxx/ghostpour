"""Measure the catalogue-scoped outside-options marker BEFORE it ships.

    .venv/bin/python qa/measure_outside_options_catalogue.py \
        [--runs "/Users/scottguida/N400 App/qa/runs"] \
        [--definition ".../form_definition_n400_tx.json"]

The auditor's rule (2026-09-22): run the extended `mark_values_outside_declared_options`
across every interviewer-lane turn in their qa/runs and report the mark count and
every marked value. If all marks are values outside the declared ids, ship it.
If any mark lands on a value that IS an id with a spelling or case difference,
that is the client's canonical form and needs a fold, not a mark.

The catalogue is built the way the client builds `metadata.choice_fields`
(`choiceFields(of:)`, their 4b55b11): `choice` fields with options, and every
`yes_no_explanation` field as yes/no. The old runs carry no catalogue, so it is
built from the definition fixture in their repo (127 fields, same count).
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.n400_interviewer_guard import mark_values_outside_declared_options  # noqa: E402

DEFAULT_RUNS = Path("/Users/scottguida/N400 App/qa/runs")
DEFAULT_DEF = Path("/Users/scottguida/N400 App/N400Helper/Sources/Resources/MockFixtures/form_definition_n400_tx.json")


def catalogue(definition: Path) -> dict[str, list[str]]:
    d = json.loads(definition.read_text())
    out = {}
    for f in d["field_catalog"]:
        if f["type"] == "choice" and f.get("options"):
            out[f["field_id"]] = list(f["options"])
        elif f["type"] == "yes_no_explanation":
            out[f["field_id"]] = ["yes", "no"]
    return out


def reply_text(w: dict) -> str | None:
    raw = w.get("raw")
    try:
        outer = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    text = outer.get("text") if isinstance(outer, dict) else None
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[1] if "\n" in text else text
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--definition", default=str(DEFAULT_DEF))
    args = ap.parse_args()
    cat = catalogue(Path(args.definition))
    print(f"catalogue: {len(cat)} fields from {Path(args.definition).name}")
    runs = 0; turns = 0; choice_facts = 0; skipped = Counter()
    marks_cat: list[dict] = []; marks_agenda = 0
    for p in sorted(Path(args.runs).glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            skipped["not json"] += 1; continue
        wire = d.get("wire") if isinstance(d, dict) else None
        if not isinstance(wire, list):
            skipped["no wire"] += 1; continue
        runs += 1
        for w in wire:
            req = w.get("request") or {}
            if not req.get("agenda"):
                skipped["turn without agenda (extractor lane)"] += 1; continue
            text = reply_text(w)
            if text is None:
                skipped["turn without a reply text"] += 1; continue
            try:
                turn = json.loads(text)
            except ValueError:
                skipped["reply not json"] += 1; continue
            if not isinstance(turn, dict):
                skipped["reply not an object"] += 1; continue
            turns += 1
            for f in turn.get("facts") or []:
                if isinstance(f, dict) and f.get("field_id") in cat:
                    choice_facts += 1
            _, off_cat = mark_values_outside_declared_options(text, req.get("agenda"), cat)
            _, off_ag = mark_values_outside_declared_options(text, req.get("agenda"))
            marks_agenda += len(off_ag)
            for o in off_cat:
                v = o["value"]
                near = None
                if isinstance(v, str):
                    key = v.strip().lower().replace(" ", "_").replace("-", "_")
                    if key in o["declared"]:
                        near = "SAME ID after case/space/hyphen fold"
                    else:
                        m = difflib.get_close_matches(key, o["declared"], n=1, cutoff=0.8)
                        if m:
                            near = f"close to id {m[0]!r}"
                marks_cat.append({"run": p.name, "turn": w.get("turn"), "field": o["field_id"],
                                  "value": v, "declared": o["declared"], "near": near})
    print(f"runs {runs}, interviewer turns {turns}, choice-field facts scanned {choice_facts}")
    for k, n in skipped.items():
        print(f"  skipped {n}: {k}")
    print(f"\nmarks with the catalogue : {len(marks_cat)}")
    print(f"marks with the agenda only: {marks_agenda}  (today's 29-gate rule, same turns)")
    by_field = Counter(m["field"] for m in marks_cat)
    for m in marks_cat:
        print(f"  {m['run']}#{m['turn']} {m['field']}={m['value']!r}  declared {m['declared']}"
              + (f"  ⚠ {m['near']}" if m["near"] else ""))
    print("\nby field:", dict(by_field))
    print("near-miss marks (a fold, not a mark):", sum(1 for m in marks_cat if m["near"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
