"""Sonnet 4.6 vs Sonnet 5 vs Haiku 4.5 (floor arm): generation step.

Replays Scott's REAL provider requests (exported from usage_log.raw_request,
model and stream stripped) against three models, byte-identical except the
model string. Provider default vs provider default: no thinking block, no
output_config, and temperature REMOVED on every arm (today's live report
lane pins 0.2 on 4.6; Sonnet 5 rejects the key and the adapter drops it,
so the no-temperature arm is what shipping Sonnet 5 on report would run).

Outputs go under tmp/eval_s46v5/out/ and are never committed. Resumable:
an (item, arm) with an existing success file is skipped.

Usage:
  .venv/bin/python tests/evals/sonnet_46_vs_5/generate.py [--limit N] [--lanes a,b]
"""
from __future__ import annotations

import argparse, asyncio, json, os, sys, time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
ITEMS = ROOT / "tmp/eval_s46v5/items/scott_items.json"
OUT = ROOT / "tmp/eval_s46v5/out"
ARMS = {
    "A": "claude-sonnet-4-6",
    "B": "claude-sonnet-5",
    "H": "claude-haiku-4-5-20251001",
}
# Report only: 10 of 12 Sonnet 5 reports hit the lane's max_tokens=4096
# (median 3310 of those tokens were thinking; three returned no visible
# text at all). "B16" is Sonnet 5 with the ceiling lifted to 16000 so the
# judged report pair compares finished reports. NOT a shipping config:
# shipping Sonnet 5 on report means raising the ceiling first.
EXTRA_ARMS = {"B16": ("claude-sonnet-5", 16000, {"report"})}
# $/M input, $/M output. Sonnet 5 at intro pricing through 2026-08-31; the
# post-intro column is what the decision is about, so both are recorded.
PRICE = {
    "claude-sonnet-4-6": (3.0, 15.0, 3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0, 3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 1.0, 5.0),
}
URL = "https://api.anthropic.com/v1/messages"


def _key() -> str:
    k = os.environ.get("CZ_ANTHROPIC_API_KEY")
    if not k:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("CZ_ANTHROPIC_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        sys.exit("no CZ_ANTHROPIC_API_KEY")
    return k


def replayable(item: dict) -> tuple[bool, str]:
    tools = item["request"].get("tools") or []
    kinds = {t.get("type") or t.get("name") for t in tools}
    if any(str(k).startswith("code_execution") for k in kinds):
        return False, "code_execution sandbox loop: not a single-shot replay"
    return True, ""


def body_for(item: dict, model: str, max_tokens: int | None = None) -> dict:
    b = json.loads(json.dumps(item["request"]))
    b["model"] = model
    if max_tokens:
        b["max_tokens"] = max_tokens
    b.pop("temperature", None)      # provider default on every arm
    b.pop("thinking", None)
    b.pop("output_config", None)
    b.pop("stream", None)
    return b


def cost(model: str, usage: dict) -> tuple[float, float]:
    pi, po, pi2, po2 = PRICE[model]
    i = (usage.get("input_tokens") or 0)
    cw = usage.get("cache_creation_input_tokens") or 0
    cr = usage.get("cache_read_input_tokens") or 0
    o = usage.get("output_tokens") or 0
    now = (i * pi + cw * pi * 1.25 + cr * pi * 0.1 + o * po) / 1e6
    post = (i * pi2 + cw * pi2 * 1.25 + cr * pi2 * 0.1 + o * po2) / 1e6
    return round(now, 6), round(post, 6)


async def run_one(client: httpx.AsyncClient, key: str, item: dict, arm: str, idx: int, sem: asyncio.Semaphore):
    if arm in ARMS:
        model, mt = ARMS[arm], None
    else:
        model, mt, _lanes = EXTRA_ARMS[arm]
    path = OUT / item["lane"] / f"{idx:02d}_{arm}.json"
    if path.exists() and json.loads(path.read_text()).get("ok"):
        return "skip"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = body_for(item, model, mt)
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with sem:
        t0 = time.monotonic()
        try:
            r = await client.post(URL, json=body, headers=headers, timeout=600)
            ms = int((time.monotonic() - t0) * 1000)
            rec = {"ok": r.status_code == 200, "status": r.status_code, "lane": item["lane"], "idx": idx,
                   "arm": arm, "model": model, "usage_log_id": item["usage_log_id"], "latency_ms": ms}
            if r.status_code == 200:
                data = r.json()
                u = data.get("usage") or {}
                now, post = cost(model, u)
                rec.update({"usage": u, "cost_now_usd": now, "cost_post_intro_usd": post,
                            "stop_reason": data.get("stop_reason"), "content": data.get("content")})
            else:
                rec["error"] = r.text[:2000]
        except Exception as e:  # network, timeout
            rec = {"ok": False, "lane": item["lane"], "idx": idx, "arm": arm, "model": model,
                   "error": repr(e)[:500], "latency_ms": int((time.monotonic() - t0) * 1000)}
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return "ok" if rec["ok"] else f"FAIL {rec.get('status')} {str(rec.get('error'))[:120]}"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--lanes", default="")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    items = json.loads(ITEMS.read_text())
    lanes = set(a.lanes.split(",")) if a.lanes else None
    key = _key()
    sem = asyncio.Semaphore(a.concurrency)
    jobs, skipped = [], []
    per_lane_idx: dict[str, int] = {}
    async with httpx.AsyncClient() as client:
        for item in items:
            idx = per_lane_idx.get(item["lane"], 0); per_lane_idx[item["lane"]] = idx + 1
            if lanes and item["lane"] not in lanes:
                continue
            ok, why = replayable(item)
            if not ok:
                skipped.append((item["lane"], idx, why)); continue
            if a.limit and idx >= a.limit:
                continue
            for arm in ARMS:
                jobs.append(run_one(client, key, item, arm, idx, sem))
            for arm, (_m, _mt, lanes_for) in EXTRA_ARMS.items():
                if item["lane"] in lanes_for:
                    jobs.append(run_one(client, key, item, arm, idx, sem))
        results = await asyncio.gather(*jobs)
    from collections import Counter
    print("results:", Counter(r.split(" ")[0] for r in results))
    for r in results:
        if r.startswith("FAIL"): print("  ", r)
    for s in skipped: print("skipped:", s)
    (OUT / "manifest.json").write_text(json.dumps({"arms": ARMS, "extra_arms": {k: list(v[:2]) + [sorted(v[2])] for k, v in EXTRA_ARMS.items()}, "skipped": skipped, "price_table": PRICE}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
