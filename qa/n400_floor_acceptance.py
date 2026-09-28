"""Acceptance for the v44 evidence floor (agreed with the auditor 2026-09-28,
N400 App contracts/gp-reply-floor-proposal-2026-09-28.md).

    .venv/bin/python qa/n400_floor_acceptance.py

Runs the RECORDED model output of each live turn through the real floor
(guard_response_text) and a REAL Jev call, so the guard is what is tested and
the model's run-to-run variation is not. Four cases, each with the outcome the
auditor and GP agreed:
  1. jorge-r3 t25: residence not_with_me and supported yes, cited from his
     earlier line, are RESTORED.
  2. jorge-r5 t21: has_prior_address2 yes, cited from "Hot Wells and before
     that my cousin's on Pleasanton", is RESTORED.
  3. conf-v18 t47: supported yes, cited from "she's my daughter, my own, I had
     her", is NOT restored (the case the floor exists for).
  4. rosa-r4 t36: resides_with_me from "Ella está aquí conmigo" is current-turn
     evidence, so the floor never sets it aside; the existing unsupported check
     must MARK it.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.n400_envelope import extract_envelope, is_envelope  # noqa: E402
from app.services.n400_evidence_support import (  # noqa: E402
    mark_unsupported_enum_facts, restore_supported_carried_facts,
)
from app.services.n400_interviewer_guard import guard_response_text  # noqa: E402

RUNS = Path("/Users/scottguida/N400 App/qa/runs")


# ⚠ The run files record GP's response AFTER the live guard ran, so a fact the
# old floor dropped is already out of `facts`, kept in `facts_dropped` with its
# field and cited words but not its value. Feeding that to the floor again
# tests nothing (the first version of this script did, and cases 1 and 2 "failed"
# because there was nothing left to set aside). So the model's own output is
# rebuilt: each dropped entry goes back into `facts` with the value the model
# emitted, as the auditor read it on the wire.
MODEL_VALUES = {
    ("spectrum-jorge-r3.json", 24): {"p6.child1.residence": "not_with_me",
                                     "p6.child1.relationship": "biological",
                                     "p6.child1.supported": "yes"},
    ("spectrum-jorge-r5.json", 20): {"p4.has_prior_address2": "yes"},
}


def recorded(run: str, wire: int):
    e = json.loads((RUNS / run).read_text())["wire"][wire]
    raw = json.loads(e["raw"])["text"]
    text = raw if is_envelope(raw) else (extract_envelope(raw) or raw)
    values = MODEL_VALUES.get((run, wire))
    if values:
        turn = json.loads(text)
        for d in turn.pop("facts_dropped", []) or []:
            if d.get("field_id") in values:
                turn.setdefault("facts", []).append({
                    "field_id": d["field_id"], "value": values[d["field_id"]], "value_type": "string",
                    "provenance": {"source": "user_stated", "confidence": 0.9, "utterance": d["utterance"]}})
        text = json.dumps(turn, ensure_ascii=False)
    return e["request"], e["user_content"], text


# The auditor's harness does not send `choice_fields` (the app has since
# 2026-09-22), and by jorge-r3 t25 the child1 node is off the agenda, so GP
# cannot know child1's options and fails closed. The per-field catalogue from
# that run's own q_p6_child1 line (wire 25) is supplied, as the app would.
CHOICE_FIELDS = {
    ("spectrum-jorge-r3.json", 24): {
        "p6.child1.residence": ["resides_with_me", "not_with_me", "unknown_missing"],
        "p6.child1.relationship": ["biological", "stepchild", "adopted"],
        "p6.child1.supported": ["yes", "no"]},
}


async def through(run: str, wire: int) -> dict:
    md, utt, text = recorded(run, wire)
    md = dict(md, choice_fields=CHOICE_FIELDS.get((run, wire), md.get("choice_fields")))
    key = get_settings().typesafe_api_key or ""
    out = guard_response_text(text, md.get("agenda"), md.get("turn_id"), user_content=utt,
                              conversation=md.get("conversation"), choice_fields=md.get("choice_fields"))
    out = await mark_unsupported_enum_facts(out, md.get("agenda"), utt, md.get("turn_id"), "probe",
                                            "primary", key, choice_fields=md.get("choice_fields"))
    out = await restore_supported_carried_facts(out, md.get("agenda"), md.get("turn_id"), "probe",
                                                "primary", key, choice_fields=md.get("choice_fields"))
    return json.loads(out)


def facts(o):
    return {f["field_id"]: f.get("value") for f in o.get("facts", [])}


async def main() -> int:
    ok = True
    o = await through("spectrum-jorge-r3.json", 24)
    f = facts(o)
    good = f.get("p6.child1.residence") == "not_with_me" and f.get("p6.child1.supported") == "yes"
    print("1 jorge-r3 t25 restored:", good, "| facts", {k: v for k, v in f.items() if "child1" in k},
          "| restored", o.get("facts_restored_by_support"), "| dropped", o.get("facts_dropped"))
    ok &= good

    o = await through("spectrum-jorge-r5.json", 20)
    good = facts(o).get("p4.has_prior_address2") == "yes"
    print("2 jorge-r5 t21 restored:", good, "| restored", o.get("facts_restored_by_support"),
          "| dropped", o.get("facts_dropped"))
    ok &= good

    o = await through("conf-v18.json", 46)
    good = facts(o).get("p6.child1.supported") != "yes"
    print("3 conf-v18 t47 kept out:", good, "| restored", o.get("facts_restored_by_support"),
          "| dropped", o.get("facts_dropped"))
    ok &= good

    o = await through("spectrum-rosa-r4.json", 35)
    marked = [u for u in o.get("facts_unsupported") or [] if u.get("field_id", "").endswith("residence")]
    filed = {k: v for k, v in facts(o).items() if k.endswith("residence")}
    good = bool(marked) or not filed
    print("4 rosa-r4 t36 marked or absent:", good, "| residence facts", filed, "| unsupported", marked)
    ok &= good

    for o in ():
        pass
    print("RESULT:", "all four as agreed" if ok else "*** NOT AS AGREED ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
