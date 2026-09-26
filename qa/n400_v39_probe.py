"""v39 pre-sync probe: one optional review at the end, and defer only what she did not say.

    .venv/bin/python qa/n400_v39_probe.py [--reps 2] [--only gate|turns] [--out FILE]

Each check runs under v38 (the served prompt, main before #1036) and v39 (this
tree), through the auditor's own client harness (`N400 App/qa/n400_qa.py`,
graph, agenda, VOLUNTEER FIELDS, known facts, minting) and GP's
guard_response_text, the way `n400_multiturn_probe.py` drives it.

  gate   (auditor's case, from 3b5d206f): at q_p8_trips_gate she says "Yes.
         India for six months, Mexico one Christmas, and Canada last summer."
         PASS: p8.trip1..3.countries minted as India, Mexico, Canada (in her
         order) and NO *.countries field deferred. v38 is expected to fail.
  turns  (the auditor's ask, turns per interview before and after): seeded
         with a full case minus Parts 10 and 11, walked from the fee question
         to interview_over by a responder that answers each question, says
         "Yes, that's right." to any read-back, and picks the form when the
         review is offered. Reports turns to the end per version. The seed
         comes from the auditor's conf-v24 run (134 facts).

Costs a few dollars of Sonnet at 2 reps.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))
import n400_multiturn_probe as mp  # noqa: E402

sys.path.insert(0, str(mp.AUDITOR_QA))
import n400_qa as q  # noqa: E402

V38_REF = "750cb9d~1"      # main immediately before v39 (#1036)
SEED_RUN = mp.AUDITOR_QA / "runs" / "conf-v24.json"
GATE_SAY = "Yes. India for six months, Mexico one Christmas, and Canada last summer."
COUNTRIES = ["india", "mexico", "canada"]


def configs(version: int) -> dict:
    out = {}
    for p in sorted((ROOT / "config/remote/n400").glob("*.json")):
        rel = f"config/remote/n400/{p.name}"
        cfg = mp.git_json(V38_REF, rel) if version == 38 else json.loads(p.read_text())
        out["n400/" + p.stem] = cfg
    got = out[mp.SLUG]["version"]
    assert got == version, f"asked for v{version}, loaded v{got}"
    return out


def seed(drop_prefixes: tuple[str, ...]) -> dict:
    facts = json.loads(SEED_RUN.read_text())["facts"]
    return {k: v for k, v in facts.items() if not k.startswith(drop_prefixes)}


def _val(v):
    return (v.get("value") if isinstance(v, dict) else v) or ""


def start(run: str, cursor: str, seed_facts: dict, runs_dir: Path) -> None:
    sp = runs_dir / f"{run}.seed.json"
    sp.write_text(json.dumps(seed_facts))
    q.cmd_start(Namespace(run=run, lane="interviewer", locale="en", persona=None, context=mp.CONTEXT,
                          volunteer=True, cursor=cursor, seed=str(sp)))


def state(run: str, runs_dir: Path) -> dict:
    return json.loads((runs_dir / f"{run}.json").read_text())


def check_gate(version: int, rep: int, runs_dir: Path) -> dict:
    run = f"v{version}-gate-{rep}"
    start(run, "q_p8_trips_gate", seed(("p8.",)), runs_dir)
    q.cmd_step(Namespace(run=run, say=GATE_SAY))
    st = state(run, runs_dir)
    last = [e for e in st["transcript"] if e.get("applicant")][-1]
    # Judged on what the LANE produced: its minted facts plus the ones the
    # harness holds as tentative because their row is not open yet (trip 2 and
    # 3 at the gate). Whether the client later commits a tentative fact is the
    # client's rule, reported separately as `committed`. The first scorer read
    # only committed facts and called v39 a fail when the lane had minted all
    # three; it also read deferred entries as dicts when the harness stores
    # plain field ids, and so showed v38's deferral as empty.
    lane = {}
    for rec in (last.get("minted") or []) + (last.get("tentative") or []):
        if isinstance(rec, dict) and str(rec.get("field_id", "")).endswith(".countries"):
            lane.setdefault(rec["field_id"], str(rec.get("value") or "").lower())
    got = [lane.get(f"p8.trip{i}.countries", "") for i in (1, 2, 3)]
    deferred = [d if isinstance(d, str) else d.get("field_id") for d in (last.get("deferred") or [])]
    facts = st.get("facts") or {}
    committed = [_val(facts.get(f"p8.trip{i}.countries")).lower() for i in (1, 2, 3)]
    minted_all = all(c in g for c, g in zip(COUNTRIES, got))
    no_country_deferred = not any(str(f).endswith(".countries") for f in deferred)
    return {"run": run, "pass": minted_all and no_country_deferred, "countries": got,
            "committed_by_client": committed, "deferred": deferred,
            "reply": (last.get("interviewer") or "")[:220]}


def respond(entry: dict, node: str | None, contact_given: bool) -> str:
    cp = entry.get("checkpoint") or {}
    text = (entry.get("interviewer") or "").lower()
    if isinstance(cp, dict) and cp.get("awaiting_confirmation") is True:
        return "Yes, that's right."
    if "straight to" in text or "once more" in text or "go through your answers" in text:
        return "Go straight to my form, please."
    if "complete and correct" in text or text.rstrip().endswith("is that right?"):
        return "Yes, that's right."
    if node and node.startswith("q_p10"):
        return "No."
    if node and node.startswith("q_p11"):
        return ("That's my only phone, and that's the only email I have." if contact_given else
                "My cell is 512 555 0147, and my email is nancy.smith@example.com.")
    return "Yes, that's right."


def check_turns(version: int, rep: int, runs_dir: Path, cap: int = 40) -> dict:
    run = f"v{version}-turns-{rep}"
    start(run, "q_p10_fee_reduction", seed(("p10.", "p11.")), runs_dir)
    contact_given, said = False, []
    for _ in range(cap):
        st = state(run, runs_dir)
        entry = st["transcript"][-1]
        if entry.get("interview_over"):
            break
        utt = respond(entry, st.get("cursor"), contact_given)
        contact_given = contact_given or "512 555 0147" in utt
        said.append(utt)
        q.cmd_step(Namespace(run=run, say=utt))
    st = state(run, runs_dir)
    over = bool(st["transcript"][-1].get("interview_over"))
    confirms = sum(1 for s in said if s == "Yes, that's right.")
    return {"run": run, "over": over, "applicant_turns": len(said), "confirmation_turns": confirms,
            "said": said}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--only", choices=["gate", "turns"])
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    from app.config import get_settings
    key = get_settings().anthropic_api_key
    runs_dir = Path(mp.tempfile.gettempdir()) / "n400-v39-probe"
    runs_dir.mkdir(parents=True, exist_ok=True)
    q.RUNS = runs_dir
    q.token = lambda: "probe-no-token"
    q.served_build = lambda: {"probe": "direct API, no health read"}
    results = {}
    for version in (38, 39):
        q.post = mp.make_post(configs(version), key, False)
        if a.only in (None, "gate"):
            results[f"v{version}-gate"] = [check_gate(version, r, runs_dir) for r in range(a.reps)]
        if a.only in (None, "turns"):
            results[f"v{version}-turns"] = [check_turns(version, r, runs_dir) for r in range(a.reps)]
    for k, rows in results.items():
        if k.endswith("gate"):
            print(f"{k}: {sum(r['pass'] for r in rows)}/{len(rows)} pass")
            for r in rows:
                print(f"   lane countries={r['countries']} client committed={r['committed_by_client']}\n"
                      f"   deferred={r['deferred']}\n   reply: {r['reply']}")
        else:
            t = [r["applicant_turns"] for r in rows]
            print(f"{k}: turns to the end {t} (median {statistics.median(t)}), "
                  f"confirmation turns {[r['confirmation_turns'] for r in rows]}, "
                  f"reached interview_over {[r['over'] for r in rows]}")
    calls = mp.CALLS
    print(f"\n{len(calls)} model calls, input {sum(c['in'] for c in calls)} tokens, "
          f"output {sum(c['out'] for c in calls)}")
    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=1))
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
