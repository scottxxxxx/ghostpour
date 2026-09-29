"""Shared runner for probes that call a paid model API (Scott's rule, 2026-09-28,
CLAUDE.md "Spending on paid model APIs": $5 per test, over that he decides).

A probe builds its requests as (custom_id, Messages API payload) pairs and hands
them to run(). The runner:

  1. ESTIMATES the cost before anything is sent, prints it, and refuses when the
     estimate is over the cap. The cap is $5 unless the caller passes a higher
     --max-spend, which is only for a run Scott has approved at that number.
  2. Sends through the MESSAGE BATCHES API by default (half price, results in
     minutes, occasionally longer). --sync sends one request at a time instead,
     for when the answer is needed now: full price, but sequential, so every
     request after the first reads the cached system prompt (the old probes ran
     8 workers in parallel, and each one wrote the cache).
  3. Reports the ACTUAL cost from the returned usage next to the estimate, and
     appends both to qa/runs/spend-ledger.jsonl with today's running total.

The estimate is deliberately pessimistic: tokens as characters / 3.5 (the v45
prompt measured 3.9), every batch request billed as a cache WRITE (a sync run
writes once per system prompt and reads after), and each response
at OUTPUT_ASSUMED tokens (the 2,298 calls of 2026-09-28 averaged about 770).
The actual cost printed afterwards is the check on it; if actual ever comes in
ABOVE the estimate, the assumptions here are wrong and must be fixed first.

Raw HTTP on purpose: GP does not install the anthropic SDK, and a qa tool is
not a reason to add a production dependency.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.anthropic.com/v1"
LEDGER = Path(__file__).resolve().parent / "runs" / "spend-ledger.jsonl"
DEFAULT_CAP = 5.0
CHARS_PER_TOKEN = 3.5
OUTPUT_ASSUMED = 1000

# $ per million tokens (input, output), first-party standard rates. A model not
# listed here cannot be estimated, so the runner refuses it rather than guess.
PRICES = {
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_WRITE, CACHE_READ, BATCH = 1.25, 0.1, 0.5


def add_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--dry", action="store_true", help="print the estimate and stop; nothing is sent")
    ap.add_argument("--sync", action="store_true",
                    help="one request at a time at full price, instead of the half-price batch")
    ap.add_argument("--max-spend", type=float, default=DEFAULT_CAP,
                    help="refuse when the estimate is over this; raise it only for a run Scott approved")
    ap.add_argument("--resume", default="", help="fetch the results of an earlier batch id instead of sending")


def usd(x: float) -> str:
    return f"${x:.2f}" if x >= 0.01 or x == 0 else f"${x:.4f}"


def _headers(key: str) -> dict:
    return {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}


def _http(method: str, url: str, key: str, body: dict | None = None, timeout: int = 240) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(key), method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 529) or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def _price(model: str) -> tuple[float, float]:
    if model not in PRICES:
        raise SystemExit(f"no price for {model!r} in qa/probe_runner.py PRICES; add it before spending")
    return PRICES[model]


def _chars(payload: dict) -> tuple[int, int]:
    system = payload.get("system") or ""
    sys_chars = len(system) if isinstance(system, str) else sum(len(b.get("text", "")) for b in system)
    msg_chars = 0
    for m in payload.get("messages", []):
        c = m.get("content")
        msg_chars += len(c) if isinstance(c, str) else sum(len(b.get("text", "")) for b in c or [])
    return sys_chars, msg_chars


def estimate(requests: list[tuple[str, dict]], batch: bool) -> float:
    # A batch runs its requests concurrently, so a cache hit there is luck:
    # every request is priced as a write. A sync run is sequential, so only the
    # first request per distinct system prompt writes and the rest read.
    total, seen = 0.0, set()
    for _, p in requests:
        pin, pout = _price(p["model"])
        sys_chars, msg_chars = _chars(p)
        key = (p["model"], json.dumps(p.get("system"), sort_keys=True))
        cached = CACHE_READ if not batch and key in seen else CACHE_WRITE
        seen.add(key)
        cost = (sys_chars / CHARS_PER_TOKEN * pin * cached
                + msg_chars / CHARS_PER_TOKEN * pin
                + min(OUTPUT_ASSUMED, p.get("max_tokens", OUTPUT_ASSUMED)) * pout) / 1e6
        total += cost * (BATCH if batch else 1.0)
    return total


def actual(model: str, usage: dict, batch: bool) -> float:
    pin, pout = _price(model)
    cost = (usage.get("input_tokens", 0) * pin
            + usage.get("cache_creation_input_tokens", 0) * pin * CACHE_WRITE
            + usage.get("cache_read_input_tokens", 0) * pin * CACHE_READ
            + usage.get("output_tokens", 0) * pout) / 1e6
    return cost * (BATCH if batch else 1.0)


def _today_total() -> float:
    if not LEDGER.exists():
        return 0.0
    today = dt.date.today().isoformat()
    total = 0.0
    for line in LEDGER.read_text().splitlines():
        row = json.loads(line)
        if row.get("date") == today:
            total += row.get("actual", 0.0)
    return total


def _ledger(script: str, n: int, mode: str, est: float, act: float, batch_id: str | None) -> float:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {"date": dt.date.today().isoformat(), "at": dt.datetime.now().isoformat(timespec="seconds"),
           "script": script, "calls": n, "mode": mode, "estimate": round(est, 6),
           "actual": round(act, 6), "batch_id": batch_id}
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return _today_total()


def _run_sync(requests, key):
    out = {}
    for i, (cid, p) in enumerate(requests, 1):
        t0 = time.monotonic()
        try:
            msg = json.loads(_http("POST", f"{API}/messages", key, p))
            out[cid] = {"message": msg, "secs": round(time.monotonic() - t0, 1)}
        except Exception as exc:  # recorded, never silently skipped
            out[cid] = {"error": repr(exc)}
        print(f"  {i}/{len(requests)} {cid}", flush=True)
    return out


def _submit_batch(requests, key) -> str:
    body = {"requests": [{"custom_id": cid, "params": p} for cid, p in requests]}
    batch = json.loads(_http("POST", f"{API}/messages/batches", key, body, timeout=600))
    return batch["id"]


def _collect_batch(batch_id: str, key: str, poll: int = 30) -> dict:
    t0 = time.monotonic()
    while True:
        b = json.loads(_http("GET", f"{API}/messages/batches/{batch_id}", key))
        counts = b.get("request_counts", {})
        if b["processing_status"] == "ended":
            break
        print(f"  batch {batch_id} {b['processing_status']} {counts} {int(time.monotonic() - t0)}s",
              flush=True)
        time.sleep(poll)
    out = {}
    for line in _http("GET", b["results_url"], key, timeout=600).decode().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)  # results arrive in any order: keyed by custom_id, never position
        res = row["result"]
        if res["type"] == "succeeded":
            out[row["custom_id"]] = {"message": res["message"]}
        else:
            out[row["custom_id"]] = {"error": f"{res['type']}: {json.dumps(res.get('error'))}"}
    return out


def run(requests: list[tuple[str, dict]], args: argparse.Namespace, key: str, script: str) -> dict | None:
    """Estimate, gate, send, and account. Returns {custom_id: {"message"|"error", ...}},
    or None for a dry run or a refusal. custom_id must match [A-Za-z0-9_-]{1,64}."""
    batch = not args.sync
    if args.resume:
        results = _collect_batch(args.resume, key)
        batch, batch_id = True, args.resume
    else:
        est = estimate(requests, batch)
        mode = "batch (half price)" if batch else "sync, sequential (full price, cache reused)"
        print(f"ESTIMATE {usd(est)} for {len(requests)} calls, {mode}; cap {usd(args.max_spend)}; "
              f"spent today so far {usd(_today_total())}", flush=True)
        if args.dry:
            print("dry run: nothing sent")
            return None
        if est > args.max_spend:
            print(f"REFUSED: {usd(est)} is over the {usd(args.max_spend)} cap. Bring Scott the estimate, "
                  "why it needs that much, and the cheaper cut (fewer cases or reps, one config) first.")
            return None
        if batch:
            batch_id = _submit_batch(requests, key)
            print(f"  submitted batch {batch_id} (if this script dies, rerun with --resume {batch_id})",
                  flush=True)
            results = _collect_batch(batch_id, key)
        else:
            batch_id = None
            results = _run_sync(requests, key)
    models = dict(requests)
    act = sum(actual(models[cid]["model"], r["message"].get("usage", {}), batch)
              for cid, r in results.items() if "message" in r and cid in models)
    est = estimate(requests, batch)
    day = _ledger(script, len(requests), "batch" if batch else "sync", est, act, batch_id)
    flag = "  *** ACTUAL OVER ESTIMATE: fix the assumptions in qa/probe_runner.py ***" if act > est else ""
    print(f"ACTUAL {usd(act)} (estimate {usd(est)}); today's total {usd(day)}{flag}", flush=True)
    return results


def text_of(message: dict) -> str:
    return "".join(b.get("text", "") for b in message.get("content", []) if b.get("type") == "text")
