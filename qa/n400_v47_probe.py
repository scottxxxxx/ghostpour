"""v47 probe (Part 9 one question at a time, contract p9-one-by-one-2026-09-29), built on the v46 probe, from LIVE v44 requests (round 7, 2026-09-28); a case may override request metadata (the pace clause the client will send), plus older regressions: the auditor's spectrum round 3 findings (2026-09-28), one case per
item, replayed from the recorded requests in `N400 App/qa/runs/`.

    .venv/bin/python qa/n400_v45_probe.py --dry          # the estimate, nothing sent
    .venv/bin/python qa/n400_v45_probe.py                # half-price batch, $5 cap
    .venv/bin/python qa/n400_v45_probe.py --sync         # now, full price, sequential

Spending goes through qa/probe_runner.py (Scott's $5 rule, 2026-09-28).

Each case sends the recorded turn's metadata and utterance through GP's own
assemble_prompt and guard_response_text (the serving path, minus the network),
calling the model through qa/probe_runner.py, once with the
SERVED config (origin/main, v39) and once with the working tree (v40). Every
raw and guarded output is saved, because a pass/fail predicate is a claim
about the text and has to be checkable by reading it.

Proved at: GP's assembly plus the model plus GP's guard. NOT proved: the
client's filing of what GP returns, and anything the live wire adds.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.n400_envelope import extract_envelope, is_envelope  # noqa: E402
from app.services.n400_interviewer_guard import guard_response_text  # noqa: E402
from app.services.prompt_assembly import assemble_prompt  # noqa: E402
from app.services.spanish_numerals import numeral_variables  # noqa: E402
from qa import probe_runner  # noqa: E402

SLUG = "n400/interviewer-turn"
RUNS = Path("/Users/scottguida/N400 App/qa/runs")

# item -> (run file, wire index). Chosen by reading each cited turn
# (2026-09-27). Item 1 uses the turn that MADE the review offer: her answer
# closes the last agenda line there, so the request still shows one line. The
# first "[agenda empty]" request comes after she has already answered the offer
# (the first cut of this probe used it and measured nothing).
ONE_FACT = "; pace: one fact per turn"
PARAGRAPHS = "; pace: full paragraphs"
FORM_DEF = Path("/private/tmp/claude-501/-Users-scottguida-cloudzap/1f240c6e-8ca1-4319-9ce9-480327eb1030/scratchpad/form_def_p9.json")  # client branch feature/p9-one-by-one
GROUPS = {"q_p9_civic", "q_p9_affiliations", "q_p9_persecution", "q_p9_armed_groups", "q_p9_crimes",
          "q_p9_immigration", "q_p9_military", "q_p9_oath_support", "q_p9_oath"}
# (name, run, wire, builder)
CASES = [
    ("A-open-en", "spectrum-amina-r7.json", 32, "open"),
    ("A-open-es", "spectrum-rosa-r1.json", 59, "open"),
    ("C-phone-filed-then-question", "spectrum-amina-r7.json", 33, "petition"),
    ("D-no-to-all-on-one-item", "spectrum-amina-r7.json", 33, "noall"),
    ("E-old-group-agenda", "spectrum-amina-r7.json", 33, "asis"),
]


def _items(locale: str, known: str) -> list[dict]:
    nodes = json.loads(FORM_DEF.read_text())["question_graph"]
    by = {n["node_id"]: n for n in nodes}
    out, nid = [], "q_p9_civic_claimed_citizen"
    while nid and nid.startswith("q_p9_") and len(out) < 80:
        n = by[nid]
        if not ("selective_service" in nid and "p9.selective_service_applies: no" in known):
            out.append(n)
        nid = n.get("default_next")
    return out


def _line(n: dict, locale: str) -> str:
    ask = n["ask"].get(locale) or n["ask"]["en"]
    return " | ".join([n["node_id"], "Part 9: Your record", ",".join(n["field_ids"]), ask, "options: yes, no"])


def _itemize(md: dict, drop: int = 0) -> dict:
    md = dict(md)
    loc = md.get("locale") or "en"
    lines = (md.get("agenda") or "").splitlines()
    keep = [l for l in lines if l.split(" | ")[0] not in GROUPS]
    first = next((i for i, l in enumerate(lines) if l.split(" | ")[0] in GROUPS), len(lines))
    pre = [l for l in keep if lines.index(l) < first]
    post = [l for l in keep if lines.index(l) >= first]
    items = [_line(n, loc) for n in _items(loc, md.get("known_facts") or "")][drop:]
    md["agenda"] = "\n".join(pre + items + post)
    return md


def build(kind: str, e: dict) -> dict:
    md, utt = dict(e["request"]), e["user_content"]
    if kind == "asis":
        return e
    if kind == "open":
        return dict(e, request=_itemize(md))
    items = _items("en", md.get("known_facts") or "")
    conv = md["conversation"].rsplit("\nINTERVIEWER:", 1)[0]
    lead = "INTERVIEWER: amina.yusuf@example.com. That's Part 11: your phone and email on file. " + items[0]["ask"]["en"]
    if kind == "petition":
        md = _itemize(md, drop=1)
        md["known_facts"] += "\np9.claimed_citizen: no"
        utt = "Hmm, I signed a petition once outside the grocery store. Does that count?"
        md["conversation"] = "\n".join([conv, lead, "APPLICANT: No.", "INTERVIEWER: " + items[1]["ask"]["en"], "APPLICANT: " + utt])
    if kind == "noall":
        md = _itemize(md, drop=2)
        md["known_facts"] += "\np9.claimed_citizen: no\np9.registered_or_voted: no"
        utt = "No to all of those, I've never done anything like that."
        md["conversation"] = "\n".join([conv, lead, "APPLICANT: No.", "INTERVIEWER: " + items[1]["ask"]["en"], "APPLICANT: No.",
                                        "INTERVIEWER: " + items[2]["ask"]["en"], "APPLICANT: " + utt])
    return dict(e, request=md, user_content=utt)


def served_config() -> dict:
    text = subprocess.run(["git", "show", "origin/main:config/remote/n400/interviewer-turn.json"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return json.loads(text)


def tree_config() -> dict:
    return json.loads((ROOT / "config/remote/n400/interviewer-turn.json").read_text())


def payload(cfg: dict, md: dict, utt: str) -> dict:
    variables = {**md, **numeral_variables(md["call_type"], md.get("locale"), utt)}
    assembled = assemble_prompt(md["call_type"], utt, {SLUG: cfg},
                                jurisdiction=md.get("jurisdiction"), variables=variables)
    return {
        "model": cfg["recommendedModel"], "max_tokens": assembled.get("max_tokens") or cfg["maxTokens"],
        "thinking": {"type": "disabled"},
        "system": [{"type": "text", "text": assembled["system_prompt"], "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": assembled["user_content"]}],
    }


def guarded(text: str, md: dict, utt: str):
    obj_text = text if is_envelope(text) else (extract_envelope(text) or text)
    out = guard_response_text(obj_text, md.get("agenda"), md.get("turn_id"),
                              user_content=utt, conversation=md.get("conversation"),
                              choice_fields=md.get("choice_fields"), locale=md.get("locale"),
                              known_facts=md.get("known_facts"))
    try:
        return json.loads(out)
    except ValueError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated case-name prefixes")
    ap.add_argument("--out", default=str(ROOT / "qa/runs/v47-probe.json"))
    probe_runner.add_args(ap)
    args = ap.parse_args(argv)
    key = get_settings().anthropic_api_key
    configs = [served_config(), tree_config()]
    print("served (origin/main) v%s, working tree v%s" % (configs[0]["version"], configs[1]["version"]))
    if configs[0]["version"] == configs[1]["version"]:
        configs = configs[1:]  # nothing to compare, and half the spend
    only = [p for p in args.only.split(",") if p]
    cases = [c for c in CASES if not only or any(c[0].startswith(p) for p in only)]
    jobs, requests = {}, []
    # Grouped by config so a --sync run reads one cached system prompt per config.
    for cfg in configs:
        for case in cases:
            name, run, idx = case[:3]
            e = build(case[3], json.loads((RUNS / run).read_text())["wire"][idx])
            for rep in range(args.reps):
                cid = f"c{len(requests):04d}"
                jobs[cid] = (name, run, idx, e, cfg, rep)
                requests.append((cid, payload(cfg, e["request"], e["user_content"])))
    got = probe_runner.run(requests, args, key, "qa/n400_v47_probe.py")
    if got is None:
        return 0
    results = []
    for cid, (name, run, idx, e, cfg, rep) in jobs.items():
        r = got.get(cid, {"error": "no result"})
        row = {"case": name, "run": run, "wire_index": idx, "turn": e["turn"],
               "user_content": e["user_content"], "rep": rep, "version": cfg["version"]}
        if "message" in r:
            msg = r["message"]
            text = probe_runner.text_of(msg)
            row.update(stop=msg.get("stop_reason"), out=msg.get("usage", {}).get("output_tokens"),
                       secs=r.get("secs"), raw=text, guarded=guarded(text, e["request"], e["user_content"]))
        else:
            row["error"] = r["error"]  # recorded, never silently skipped
        results.append(row)
    results.sort(key=lambda r: (r["case"], r["version"], r["rep"]))
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("saved", args.out, len(results), "calls, errors", sum("error" in r for r in results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
