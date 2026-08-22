"""Build the blind judging pack and the separate key.

- Judged subset chosen by SEEDED random sampling per lane (code, not a
  human reading outputs). Seed recorded in the key.
- Each pair: two outputs side by side, labelled Left/Right, model ids
  stripped, left/right order randomized per pair. Forced choice with
  "no difference" permitted. No per-arm scores.
- Pairs: 4.6 vs 5 on the judged subset; Haiku floor pairs (Haiku vs a
  randomly chosen Sonnet) on 2 items per lane, interleaved so Scott
  cannot tell floor pairs from decision pairs.
- The key (which arm was Left) lives in key.json, NOT in the HTML.
- Scott records choices in choices.txt as lines: `<pair_id> L|R|=`.

Usage: .venv/bin/python tests/evals/sonnet_46_vs_5/build_pack.py [--seed 20260821]
"""
from __future__ import annotations

import argparse, html, json, random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "tmp/eval_s46v5/out"
PACK = ROOT / "tmp/eval_s46v5/pack"
JUDGED = {"project_chat": 9, "meeting_chat": 9, "report": 5, "analysis": 5}
# Report: the Sonnet 5 arm judged is B16 (ceiling lifted to 16000) because
# at the lane's live 4096 ten of twelve Sonnet 5 reports were truncated.
# The judge sees no difference in the pack; the key and tally say it.
SONNET5_ARM = {"report": "B16"}
FLOOR_PER_LANE = 2
MINUTES = {"analysis": 5, "project_chat": 8.5, "meeting_chat": 9.5, "report": 14.5}


def load(lane: str, idx: int, arm: str) -> dict | None:
    p = OUT / lane / f"{idx:02d}_{arm}.json"
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    return r if r.get("ok") else None


def text_of(rec: dict) -> str:
    parts = []
    for c in rec.get("content") or []:
        if c.get("type") == "text":
            parts.append(c["text"])
        elif c.get("type") == "tool_use":
            parts.append("[tool call: " + c.get("name", "?") + "]\n" + json.dumps(c.get("input"), indent=1, ensure_ascii=False)[:20000])
    return "\n".join(parts).strip()


def prompt_of(lane: str, idx: int) -> str:
    items = json.loads((ROOT / "tmp/eval_s46v5/items/scott_items.json").read_text())
    per = [x for x in items if x["lane"] == lane]
    rq = per[idx]["request"]
    msg = rq["messages"][-1]["content"]
    if isinstance(msg, list):
        msg = "\n".join(p.get("text", "") for p in msg if isinstance(p, dict) and p.get("type") == "text")
    return msg


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seed", type=int, default=20260821)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    pairs, key = [], {"seed": a.seed, "pairs": {}}
    for lane, n in JUDGED.items():
        b_arm = SONNET5_ARM.get(lane, "B")
        avail = sorted({int(p.name[:2]) for p in (OUT / lane).glob("*_A.json")
                        if load(lane, int(p.name[:2]), "A") and load(lane, int(p.name[:2]), b_arm)})
        chosen = sorted(rng.sample(avail, min(n, len(avail))))
        for idx in chosen:
            pairs.append((lane, idx, "A", b_arm, "decision"))
        floor_idx = rng.sample(chosen, min(FLOOR_PER_LANE, len(chosen)))
        for idx in floor_idx:
            if load(lane, idx, "H"):
                pairs.append((lane, idx, "H", rng.choice(["A", b_arm]), "floor"))
    rng.shuffle(pairs)
    PACK.mkdir(parents=True, exist_ok=True)
    sections, est = [], {}
    for i, (lane, idx, x, y, kind) in enumerate(pairs, 1):
        pid = f"P{i:02d}"
        left, right = (x, y) if rng.random() < 0.5 else (y, x)
        key["pairs"][pid] = {"lane": lane, "idx": idx, "left": left, "right": right, "kind": kind}
        est[lane] = est.get(lane, 0) + MINUTES[lane]
        lt, rt = text_of(load(lane, idx, left)), text_of(load(lane, idx, right))
        sections.append(f"""
<section id="{pid}"><h2>{pid} <span class="lane">{html.escape(lane)}</span></h2>
<details><summary>The ask (same for both)</summary><pre>{html.escape(prompt_of(lane, idx)[:6000])}</pre></details>
<div class="pair"><div><h3>Left</h3><pre>{html.escape(lt)}</pre></div><div><h3>Right</h3><pre>{html.escape(rt)}</pre></div></div>
<p class="choice">Record: <code>{pid} L</code> (Left better) · <code>{pid} R</code> (Right better) · <code>{pid} =</code> (no difference)</p>
</section>""")
    total = sum(est.values())
    page = f"""<!doctype html><meta charset="utf-8"><title>Blind pack</title>
<style>body{{font:15px/1.45 -apple-system,system-ui,sans-serif;max-width:1400px;margin:2rem auto;padding:0 1rem}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}} pre{{white-space:pre-wrap;background:#f6f6f6;padding:1rem;border-radius:8px;max-height:70vh;overflow:auto}}
.lane{{font-weight:normal;color:#666;font-size:.8em}} section{{border-top:2px solid #ddd;margin-top:3rem}} .choice{{background:#fffbe6;padding:.5rem 1rem;border-radius:6px}}</style>
<h1>Sonnet lane pack, {len(pairs)} pairs</h1>
<p>Two answers to the same ask. Pick the better one or call it a tie. Model names are stripped and order is random per pair. Write one line per pair in <code>choices.txt</code> next to this file, e.g. <code>P07 R</code>. You can stop and resume any time; unjudged pairs are just left out of the tally.</p>
<p>Estimated reading: {' · '.join(f'{l} {m:.0f} min' for l, m in est.items())} · total about {total/60:.1f} h.</p>
{''.join(sections)}"""
    (PACK / "pack.html").write_text(page)
    (PACK / "key.json").write_text(json.dumps(key, indent=1))
    (PACK / "choices.txt").touch()
    print(f"{len(pairs)} pairs -> {PACK}/pack.html ; key in key.json ; est {total/60:.1f} h")
    for l, m in est.items(): print(f"  {l}: {m:.0f} min")


if __name__ == "__main__":
    main()
