"""v42 probe (v41 cases plus round 4): the auditor's spectrum round 3 findings (2026-09-28), one case per
item, replayed from the recorded requests in `N400 App/qa/runs/`.

    .venv/bin/python qa/n400_v42_probe.py [--reps 3] [--out qa/runs/v41-probe.json]

Each case sends the recorded turn's metadata and utterance through GP's own
assemble_prompt and guard_response_text (the serving path, minus the network),
calling the model directly the way qa/n400_v39_probe.py does, once with the
SERVED config (origin/main, v39) and once with the working tree (v40). Every
raw and guarded output is saved, because a pass/fail predicate is a claim
about the text and has to be checkable by reading it.

Proved at: GP's assembly plus the model plus GP's guard. NOT proved: the
client's filing of what GP returns, and anything the live wire adds.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.n400_envelope import extract_envelope, is_envelope  # noqa: E402
from app.services.n400_interviewer_guard import guard_response_text  # noqa: E402
from app.services.prompt_assembly import assemble_prompt  # noqa: E402
from app.services.spanish_numerals import numeral_variables  # noqa: E402

SLUG = "n400/interviewer-turn"
RUNS = Path("/Users/scottguida/N400 App/qa/runs")

# item -> (run file, wire index). Chosen by reading each cited turn
# (2026-09-27). Item 1 uses the turn that MADE the review offer: her answer
# closes the last agenda line there, so the request still shows one line. The
# first "[agenda empty]" request comes after she has already answered the offer
# (the first cut of this probe used it and measured nothing).
CASES = [   # (item, run, wire index = transcript turn - 1), read by the fork 2026-09-28
    ("1-sweep-rosa", "spectrum-rosa-r3.json", 66),
    ("1-sweep-jorge", "spectrum-jorge-r3.json", 52),
    ("2-readback-guarded", "spectrum-jorge-r3.json", 24),
    ("3-job-in-passing", "spectrum-jorge-r3.json", 25),
    ("4-city-not-zip", "spectrum-jorge-r3.json", 31),
    ("5-part9-recap", "spectrum-priya-r4.json", 22),
    ("6-refinement", "spectrum-jorge-r3.json", 16),
    ("7-surname-amina", "spectrum-amina-r3.json", 24),
    ("7-surname-rosa", "spectrum-rosa-r3.json", 35),
    ("8-five-years-addr", "spectrum-priya-r4.json", 5),
    ("8-five-years-job", "spectrum-priya-r4.json", 8),
    ("10-group-part2", "spectrum-amina-r3.json", 8),
    ("13-short-repeat", "spectrum-minh-r3.json", 58),
    ("14-como-answer", "spectrum-rosa-r3.json", 16),
    ("15-no-inference", "spectrum-rosa-r3.json", 39),
    ("16-register", "spectrum-rosa-r3.json", 36),
    ("8-five-years-addr-live", "spectrum-priya-r5.json", 5),   # round 4, live on v41
]


def served_config() -> dict:
    text = subprocess.run(["git", "show", "origin/main:config/remote/n400/interviewer-turn.json"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return json.loads(text)


def tree_config() -> dict:
    return json.loads((ROOT / "config/remote/n400/interviewer-turn.json").read_text())


def call(cfg: dict, md: dict, utt: str, key: str) -> dict:
    variables = {**md, **numeral_variables(md["call_type"], md.get("locale"), utt)}
    assembled = assemble_prompt(md["call_type"], utt, {SLUG: cfg},
                                jurisdiction=md.get("jurisdiction"), variables=variables)
    payload = {
        "model": cfg["recommendedModel"], "max_tokens": assembled.get("max_tokens") or cfg["maxTokens"],
        "thinking": {"type": "disabled"},
        "system": [{"type": "text", "text": assembled["system_prompt"], "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": assembled["user_content"]}],
    }
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json", "x-api-key": key,
                                          "anthropic-version": "2023-06-01"}, method="POST")
    t0 = time.monotonic()
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                resp = json.loads(r.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 529) or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    obj_text = text if is_envelope(text) else (extract_envelope(text) or text)
    guarded = guard_response_text(obj_text, md.get("agenda"), md.get("turn_id"),
                                  user_content=utt, conversation=md.get("conversation"))
    try:
        env = json.loads(guarded)
    except ValueError:
        env = None
    u = resp.get("usage", {})
    return {"version": cfg["version"], "stop": resp.get("stop_reason"), "out": u.get("output_tokens"),
            "secs": round(time.monotonic() - t0, 1), "raw": text, "guarded": env}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated case-name prefixes")
    ap.add_argument("--out", default=str(ROOT / "qa/runs/v42-probe.json"))
    args = ap.parse_args(argv)
    key = get_settings().anthropic_api_key
    configs = [served_config(), tree_config()]
    assert configs[0]["version"] == 41 and configs[1]["version"] == 42, [c["version"] for c in configs]
    only = [p for p in args.only.split(",") if p]
    cases = [c for c in CASES if not only or any(c[0].startswith(p) for p in only)]
    jobs = []
    for name, run, idx in cases:
        e = json.loads((RUNS / run).read_text())["wire"][idx]
        for cfg in configs:
            for rep in range(args.reps):
                jobs.append((name, run, idx, e, cfg, rep))
    results = []
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(call, cfg, e["request"], e["user_content"], key): (name, run, idx, e, rep)
                for name, run, idx, e, cfg, rep in jobs}
        for fut in cf.as_completed(futs):
            name, run, idx, e, rep = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # recorded, never silently skipped
                res = {"error": repr(exc)}
            results.append({"case": name, "run": run, "wire_index": idx, "turn": e["turn"],
                            "user_content": e["user_content"], "rep": rep, **res})
            print(f"{name:22s} v{res.get('version', '?')} rep{rep} stop={res.get('stop')} out={res.get('out')}",
                  flush=True)
    results.sort(key=lambda r: (r["case"], r.get("version", 0), r["rep"]))
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("saved", args.out, len(results), "calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
