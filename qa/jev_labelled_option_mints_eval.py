"""The auditor's hand-labelled option mints, through the PRODUCTION check.

    .venv/bin/python qa/jev_labelled_option_mints_eval.py [--dry] [--runs DIR] [--out FILE]

`qa/labelled-option-mints.json` (auditor, 2026-09-21) is 22 real turns from the
graded runs: utterance verbatim, the minted choice field and value, and a mark.
Unlike `jev_evidence_support_eval.py`, whose cases are synthetic and hand
assembled, every check here is built by `enum_facts_to_check` from the turn's
own wire record (the agenda the client sent, the fact's cited words, what she
said), so the scope rule is under test too: a case the production function
declines to send to Jev is reported as NOT CHECKED rather than silently passed.

Three marks, graded apart because they do not cost the same:
  supported    her words on that turn establish it; a mark is a FALSE MARK.
  unsupported  they do not; no mark is a MISS (today's silent guess).
  earlier_turn the value is true on the case and a bare "Yes." on a read-back
               minted it. On user_content alone the evidence rule SHOULD flag
               these; whether that is right is a policy question (the auditor's
               view: flag here, let the client drop flags on confirmation
               turns). Counted, never scored.

The wire records live in the auditor's repo, so `--runs` points there.

THE CATALOGUE (2026-09-24). Production scopes by the client's per-field
`metadata.choice_fields` since #1021, and these runs predate that field, so
the first run (09-22) checked only 16 of 22 under the old agenda scope. The
catalogue is rebuilt here from the client's own form definition, the same
walk as `InterviewEngine.choiceFields(of:)` (a choice field's options, a
yes/no field as yes and no), which gives the 127 fields the wire carries.
`--no-catalogue` restores the agenda-only scope for comparison.

`--variant NAME` puts a candidate question set from `jev_support_variants.py`
to Jev in place of production's `support_questions`, and `--reps N` asks
each check N times, because a single run cannot tell a question that moved
the answer from one that drew a different sample.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import n400_evidence_support as es  # noqa: E402

HERE = Path(__file__).resolve().parent
LABELS = HERE / "labelled-option-mints.json"
DEFAULT_RUNS = Path("/Users/scottguida/N400 App/qa/runs")
DEFAULT_FORM = Path("/Users/scottguida/N400 App/N400Helper/Sources/Resources/MockFixtures/"
                    "form_definition_n400_tx.json")


def choice_fields_from_form(path: Path) -> dict[str, list[str]]:
    """What the client sends as `metadata.choice_fields`, rebuilt from its form."""
    out = {}
    for f in json.loads(path.read_text())["field_catalog"]:
        if f.get("type") == "choice" and f.get("options"):
            out[f["field_id"]] = list(f["options"])
        elif f.get("type") == "yes_no_explanation":
            out[f["field_id"]] = ["yes", "no"]
    return out


def wire_turn(run: dict, turn_no: int, utterance: str) -> dict:
    hits = [w for w in run["wire"] if w.get("turn") == turn_no]
    if len(hits) != 1:
        raise SystemExit(f"turn {turn_no}: {len(hits)} wire records")
    w = hits[0]
    # The labels quote some utterances truncated or with a paraphrased tail;
    # the wire text is the authority. The first 30 characters must agree so
    # the record is the right turn, and any difference is REPORTED, since a
    # label that was read against different words than Jev sees is a finding.
    wire_text = (w.get("user_content") or "").strip()
    if wire_text[:30] != utterance.strip()[:30]:
        raise SystemExit(f"turn {turn_no}: not the same turn\n  label: {utterance!r}\n  wire:  {wire_text!r}")
    w["_label_differs"] = wire_text != utterance.strip()
    return w


def reply_json(w: dict) -> dict:
    raw = w.get("raw")
    outer = json.loads(raw) if isinstance(raw, str) else raw
    text = outer.get("text") if isinstance(outer, dict) else None
    if isinstance(text, str):
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[1] if "\n" in text else text
        return json.loads(text)
    return outer


def scope_reason(fact: dict | None, agenda: str | None, c: dict, choice_fields=None) -> str:
    """Why the production check did not send this fact to Jev. Same tests as
    `enum_facts_to_check`, spelled out one at a time so the report can say."""
    from app.services.n400_interviewer_guard import _norm, agenda_field_ids, agenda_options
    if fact is None:
        return "fact not in the reply"
    if choice_fields:
        declared = choice_fields.get(c["field"])
        if not declared:
            return "not a choice field in the client's catalogue"
        if c["value"].lower() not in [o.lower() for o in declared]:
            return (f"value is OUTSIDE the field's options {declared} "
                    "(the outside-options guard's case, not this one)")
        cited = ((fact.get("provenance") or {}).get("utterance")) or ""
        if _norm(c["value"]) and _norm(c["value"]) in _norm(cited):
            return f"value appears literally in the cited words {cited!r} (the evidence floor's case)"
        return "declined for a reason this report does not model"
    if not agenda:
        return "the request carries no agenda at all (an extractor-lane run; out of scope for an agenda-scoped check)"
    options, fields = agenda_options(agenda), agenda_field_ids(agenda)
    nodes = [n for n, ids in fields.items() if c["field"] in ids]
    if not nodes:
        return ("no agenda line lists the field: a folded gate or a volunteered value from a later node, "
                "which the client folds BY DESIGN (auditor, 2026-09-22); invisible to every GP guard today")
    declared = {o for n in nodes for o in options.get(n, set())}
    if not declared:
        return "the agenda line declares no options"
    if c["value"].lower() not in declared:
        return f"value is OUTSIDE the declared options {sorted(declared)} (the outside-options guard's case, not this one)"
    cited = ((fact.get("provenance") or {}).get("utterance")) or ""
    if _norm(c["value"]) and _norm(c["value"]) in _norm(cited):
        return f"value appears literally in the cited words {cited!r} (the evidence floor's case)"
    return "declined for a reason this report does not model"


def build_checks(runs_dir: Path, choice_fields=None) -> list[dict]:
    labels = json.loads(LABELS.read_text())
    out = []
    for c in labels["cases"]:
        run = json.loads((runs_dir / c["run"]).read_text())
        w = wire_turn(run, c["turn"], c["utterance"])
        agenda = (w.get("request") or {}).get("agenda")
        reply = reply_json(w)
        facts = reply.get("facts") or []
        fact = next((f for f in facts if f.get("field_id") == c["field"]
                     and str(f.get("value", "")).strip().lower() == c["value"].lower()), None)
        checks = es.enum_facts_to_check(reply, agenda, w.get("user_content"), choice_fields)
        mine = [k for k in checks if k["field_id"] == c["field"]]
        out.append({"case": c, "fact_in_reply": fact is not None, "label_differs": w["_label_differs"],
                    "wire_text": w.get("user_content"),
                    "cited": ((fact or {}).get("provenance") or {}).get("utterance"),
                    "check": mine[0] if mine else None,
                    "why_not": None if mine else scope_reason(fact, agenda, c, choice_fields)})
    return out


async def judge(key: str, checks: list[dict], questions=None, reps: int = 1) -> list[dict]:
    from app.services import typesafe_judge as tj
    questions = questions or es.support_questions
    rows = []
    for item in checks:
        k = item["check"]
        c = item["case"]
        row = {"id": f"{c['run']}#{c['turn']}:{c['field']}", "mark": c["mark"], "locale": c["locale"],
               "field": c["field"], "value": c["value"], "checked": k is not None, "why_not": item["why_not"]}
        if k is not None:
            samples = []
            for _ in range(reps):
                body, meta = await tj.guarded_ask(key, {"facts": [k]}, questions(1),
                                                  judgment="eval", mode="eval", timeout=10)
                a = (body or {}).get("answers", {}).get("f0", {})
                samples.append({"marked": bool(es.read_support(body, [k])) if body else False,
                                "choice": a.get("choice"),
                                "confidence": round(float(a.get("confidence") or 0), 3),
                                "p_supports": round(float((a.get("probabilities") or {}).get("supports", 0)), 3),
                                "ms": meta.get("jev_ms"), "input_tokens": meta.get("input_tokens")})
            # The row reads as its MAJORITY; every sample is kept so a split is visible.
            marks = sum(s["marked"] for s in samples)
            first = samples[0]
            row.update({"marked": marks * 2 > len(samples), "marks": f"{marks}/{len(samples)}",
                        "choice": first["choice"], "confidence": first["confidence"],
                        "samples": [(s["choice"], s["confidence"]) for s in samples],
                        "p_supports": [s["p_supports"] for s in samples],
                        "ms": first["ms"], "input_tokens": sum(s["input_tokens"] or 0 for s in samples)})
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="build the checks, call nothing")
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--out")
    ap.add_argument("--form", default=str(DEFAULT_FORM), help="the client's form definition")
    ap.add_argument("--no-catalogue", action="store_true", help="agenda-only scope, as before #1021")
    ap.add_argument("--variant", default="prod", help="a question set in jev_support_variants.py")
    ap.add_argument("--reps", type=int, default=1)
    args = ap.parse_args()
    choice_fields = None if args.no_catalogue else choice_fields_from_form(Path(args.form))
    if choice_fields is not None:
        print(f"catalogue: {len(choice_fields)} choice fields from {args.form}")
    checks = build_checks(Path(args.runs), choice_fields)
    print(f"{len(checks)} labelled cases, {sum(1 for c in checks if c['check'])} reach Jev under production scope")
    for item in checks:
        c = item["case"]
        tag = "CHECK" if item["check"] else f"SKIP ({item['why_not']})"
        print(f"  {c['mark']:<12} {c['locale']} {c['field']}={c['value']:<28} {tag}")
        if item["label_differs"]:
            print(f"      LABEL TEXT DIFFERS from the wire; Jev sees the wire: {item['wire_text']!r}")
        if item["check"]:
            k = item["check"]
            print(f"      q: {k['question'][:90]!r}\n      cited: {k['cited_words']!r}  options: {k['options']}")
    if args.dry:
        return 0
    from app.config import get_settings
    from app.services import typesafe_judge as tj
    key = get_settings().typesafe_api_key
    if not key:
        print("needs CZ_TYPESAFE_API_KEY in .env")
        return 2
    questions = None
    if args.variant != "prod":
        sys.path.insert(0, str(HERE))
        import jev_support_variants as variants
        questions = variants.VARIANTS[args.variant]
    print(f"variant {args.variant}, {args.reps} rep(s) per check")
    rows = asyncio.run(judge(key, checks, questions, args.reps))
    by = lambda m: [r for r in rows if r["mark"] == m and r["checked"]]  # noqa: E731
    uns, sup, earl = by("unsupported"), by("supported"), by("earlier_turn")
    missed = [r for r in uns if not r["marked"]]
    false = [r for r in sup if r["marked"]]
    print(f"\nfloor {tj.CONFIDENCE_FLOOR}, hand labels are the authority")
    print(f"unsupported mints caught : {len(uns) - len(missed)}/{len(uns)}")
    for r in missed:
        print(f"    MISSED     {r['id']}: {r['marks']} marked, {r['samples']}")
    print(f"good mints left alone    : {len(sup) - len(false)}/{len(sup)}")
    for r in false:
        print(f"    FALSE MARK {r['id']}: {r['marks']} marked, {r['samples']}")
    print(f"earlier_turn flagged     : {sum(1 for r in earl if r['marked'])}/{len(earl)}  (policy, not scored)")
    for r in earl:
        print(f"    {'FLAG' if r['marked'] else 'pass'}       {r['id']}: {r['marks']} marked, {r['samples']}")
    # The read-side lever beside the question: Jev's whole distribution, not
    # one label's confidence. Printed per case so a threshold is chosen by
    # looking at where the two marks fall, never tuned blind.
    print("\nP(supports), lowest first (a mark under a P threshold would take every row above the cut line)")
    for r in sorted((r for r in rows if r["checked"]), key=lambda r: statistics.median(r["p_supports"])):
        print(f"    {statistics.median(r['p_supports']):.2f}  {r['mark']:<12} {r['id']}")
    skipped = [r for r in rows if not r["checked"]]
    print(f"not checked (scope)      : {len(skipped)}")
    for r in skipped:
        print(f"    {r['mark']:<12} {r['id']}: {r['why_not']}")
    ms = [r["ms"] for r in rows if r.get("ms") is not None]
    if ms:
        print(f"\nmedian {int(statistics.median(ms))}ms  max {max(ms)}  input tokens total {sum(r.get('input_tokens') or 0 for r in rows)}")
    if args.out:
        Path(args.out).write_text(json.dumps({"model": tj.MODEL, "floor": tj.CONFIDENCE_FLOOR,
                                              "variant": args.variant, "reps": args.reps,
                                              "catalogue": choice_fields is not None, "rows": rows},
                                             ensure_ascii=False, indent=1))
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
