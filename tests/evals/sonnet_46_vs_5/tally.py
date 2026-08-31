"""Tally choices.txt against key.json. Code counts; nothing is inferred.

Reports, in this order: (1) Haiku floor separation rate, (2) per-lane
4.6 vs 5 preference with n and what it cannot support, (3) the
alongside table (billed tokens, cost now and post-intro, latency) over
ALL generated items, not just the judged subset.
"""
from __future__ import annotations

import json, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "tmp/eval_s46v5/out"
PACK = ROOT / "tmp/eval_s46v5/pack"
NAME = {"A": "sonnet-4-6", "B": "sonnet-5", "H": "haiku-4-5", "B16": "sonnet-5@16k"}


def main():
    key = json.loads((PACK / "key.json").read_text())["pairs"]
    choices = {}
    for line in (PACK / "choices.txt").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in key and parts[1] in ("L", "R", "="):
            choices[parts[0]] = parts[1]
    # 1. floor
    floor_total = floor_sonnet_won = floor_tie = 0
    for pid, k in key.items():
        if k["kind"] != "floor" or pid not in choices:
            continue
        floor_total += 1
        c = choices[pid]
        if c == "=":
            floor_tie += 1; continue
        winner = k["left"] if c == "L" else k["right"]
        if winner != "H":
            floor_sonnet_won += 1
    print("== 1. FLOOR (Haiku vs a Sonnet), judged %d of %d floor pairs" % (floor_total, sum(1 for k in key.values() if k["kind"] == "floor")))
    if floor_total:
        print("   Sonnet preferred %d, Haiku preferred %d, no difference %d  => separation rate %.0f%%" % (
            floor_sonnet_won, floor_total - floor_sonnet_won - floor_tie, floor_tie, 100 * floor_sonnet_won / floor_total))
        if floor_sonnet_won / floor_total < 0.75:
            print("   STOP CONDITION: judging does not reliably separate Haiku from the Sonnets; the 4.6-vs-5 result below is not evidence of quality.")
    # 2. per lane
    print("== 2. SONNET 4.6 vs SONNET 5, per lane (decision pairs)")
    per = defaultdict(lambda: {"A": 0, "B": 0, "=": 0, "n_total": 0})
    for pid, k in key.items():
        if k["kind"] != "decision":
            continue
        per[k["lane"]]["n_total"] += 1
        if pid not in choices:
            continue
        c = choices[pid]
        if c == "=":
            per[k["lane"]]["="] += 1
        else:
            w = k["left"] if c == "L" else k["right"]
            per[k["lane"]]["B" if w == "B16" else w] += 1
    for lane, d in per.items():
        n = d["A"] + d["B"] + d["="]
        lead = abs(d["A"] - d["B"])
        verdict = "NOISE at this n" if n == 0 or lead <= max(1, round(n ** 0.5)) else ("4.6 preferred" if d["A"] > d["B"] else "5 preferred")
        print(f"   {lane:14s} judged {n}/{d['n_total']}: 4.6 {d['A']}, 5 {d['B']}, tie {d['=']}  -> {verdict}"
              + ("   [report: Sonnet 5 arm is B16 (max_tokens 16000; at the live 4096 10/12 were truncated). 4.6 arm is NOT live 4.6, which pins temperature 0.2]" if lane == "report" else ""))
    # 3. alongside, all generated
    print("== 3. ALONGSIDE, all generated items (not a verdict)")
    agg = defaultdict(lambda: defaultdict(list))
    for p in OUT.glob("*/*.json"):
        r = json.loads(p.read_text())
        if not r.get("ok"):
            continue
        u = r["usage"]; a = agg[r["lane"]][r["arm"]]
        det = u.get("output_tokens_details") or {}
        think = det.get("thinking_tokens") if isinstance(det, dict) else None
        if think is None:
            think = u.get("output_tokens_details.thinking_tokens")
        a.append((u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                  u.get("output_tokens", 0), r["cost_now_usd"], r["cost_post_intro_usd"], r["latency_ms"], think or 0,
                  r.get("stop_reason")))
    print(f"   {'lane':14s} {'arm':10s} {'n':>3s} {'in_tok':>7s} {'out_tok':>7s} {'$now':>8s} {'$post':>8s} {'p50 ms':>7s}   (medians; thinking tokens as a distribution)")
    for lane, arms in agg.items():
        for arm in ("A", "B", "B16", "H"):
            v = arms.get(arm)
            if not v: continue
            med = lambda i: statistics.median(x[i] for x in v)
            th = [x[5] for x in v]
            print(f"   {lane:14s} {NAME[arm]:10s} {len(v):3d} {med(0):7.0f} {med(1):7.0f} {med(2):8.4f} {med(3):8.4f} {med(4):7.0f}"
                  f"   thinking min/med/max {min(th)}/{int(statistics.median(th))}/{max(th)}"
                  f"   max_tokens hits {sum(1 for x in v if x[6] == 'max_tokens')}/{len(v)}")


if __name__ == "__main__":
    main()
