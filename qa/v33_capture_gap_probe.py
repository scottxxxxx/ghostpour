"""Pre-ship probe for v33's capture gap rule, as pre-registered by fable-auditor-f5.

    .venv/bin/python qa/v33_capture_gap_probe.py --dry     # assemble only, no API calls
    .venv/bin/python qa/v33_capture_gap_probe.py            # 5 runs x 3 reps, direct API

Inputs are the auditor's exact turn-2 requests from `N400 App/qa/runs/v32-*.json`
(the harness wire, recorded against the live v32 lane on 2026-09-15). Each is
replayed against the v33 prompt from THIS checkout's bundle, assembled the way
`app/routers/chat.py` assembles it: the whole request bag as variables,
jurisdiction passed, the Spanish numeral merge applied.

Transport is the Anthropic API directly, as ruled (option 1): the served config's
`thinking` overrides the request on the gateway, and a v33 arm there would mean
serving v33. `thinking` is sent EXPLICITLY as disabled, matching the served
config; an omitted field on Sonnet 5 means thinking on.

PASS, pre-registered: 9 of 9 gap turns (DUI, Selective Service, speeding ticket,
3 reps each) with exactly one `deferred` entry, origin "capture_gap", on the named
p9 id; and 3 of 3 phone controls minting `p11.mobile_phone` with no deferred
entry. `v32-gap-selective-2` is the same Selective Service wording on a second
case and is scored SEPARATELY, outside the pre-registered 9.

Scored on the MODEL's raw output first. GP's `guard_response_text` is then
applied to the same text and scored again, so a guard that would change the
wire is visible, not assumed away. The route's retry guards (checkpoint,
oath, closing) are not exercised.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUNS = Path("/Users/scottguida/N400 App/qa/runs")
CASES = [
    # (run file, expected p9 id or None for the control, in the pre-registered 9?)
    ("v32-first-capture-gap", "p9.arrested_ever", True),
    ("v32-gap-selective", "p9.selective_service_registered", True),
    ("v32-gap-ticket", "p9.arrested_ever", True),
    ("v32-inwindow-phone", None, True),
    ("v32-gap-selective-2", "p9.selective_service_registered", False),
]
REPS = 3
SLUG = "n400/interviewer-turn"


def bundle_configs() -> dict:
    """Every n400 bundle file under its slug, straight from the repo. Never the
    local data overlay, which can shadow the bundle."""
    out = {}
    for p in sorted((ROOT / "config/remote/n400").glob("*.json")):
        out["n400/" + p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return out


def turn2_request(run: str) -> dict:
    doc = json.loads((RUNS / f"{run}.json").read_text(encoding="utf-8"))
    wire = [w for w in doc["wire"] if w.get("turn") == 2]
    assert len(wire) == 1, (run, len(wire))
    return wire[0]


def call(model: str, key: str, system: str, user: str, max_tokens: int) -> tuple[dict, int]:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    return resp, int((time.monotonic() - t0) * 1000)


def parse(text: str):
    from app.services.n400_envelope import extract_envelope, is_envelope
    body = text if is_envelope(text) else (extract_envelope(text) or text)
    try:
        return json.loads(body)
    except Exception:
        return None


def score(obj, expected_id):
    if obj is None:
        return False, "UNPARSEABLE"
    deferred = [d for d in (obj.get("deferred") or []) if isinstance(d, dict)]
    facts = [f.get("field_id") for f in (obj.get("facts") or []) if isinstance(f, dict)]
    if expected_id is None:
        ok = "p11.mobile_phone" in facts and not deferred
        return ok, f"facts={facts} deferred={len(deferred)}"
    gaps = [d for d in deferred if d.get("origin") == "capture_gap" and d.get("field_id") == expected_id]
    ok = len(deferred) == 1 and len(gaps) == 1
    detail = [(d.get("field_id"), d.get("origin"), d.get("partial_value")) for d in deferred]
    return ok, f"deferred={detail}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from app.config import get_settings
    from app.services.n400_interviewer_guard import guard_response_text
    from app.services.prompt_assembly import assemble_prompt
    from app.services.spanish_numerals import numeral_variables

    configs = bundle_configs()
    cfg = configs[SLUG]
    model = cfg["recommendedModel"]
    print(f"prompt v{cfg['version']} chars {len(cfg['systemPrompt'])} model {model} thinking disabled (explicit)")
    assert cfg["version"] == 33, "this probe is for v33; checkout is on the wrong branch"

    key = "" if args.dry else get_settings().anthropic_api_key
    if not args.dry:
        assert key, "no Anthropic key in settings"

    rows = []
    totals = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
    for run, expected, preregistered in CASES:
        w = turn2_request(run)
        req, utterance = w["request"], w["user_content"]
        variables = {**req, **numeral_variables(req["call_type"], req.get("locale"), utterance)}
        assembled = assemble_prompt(req["call_type"], utterance, configs,
                                    jurisdiction=req.get("jurisdiction"), variables=variables)
        assert assembled, run
        max_tokens = assembled.get("max_tokens") or cfg.get("maxTokens") or 2048
        if args.dry:
            print(f"  DRY {run}: system {len(assembled['system_prompt'])} chars, user {len(assembled['user_content'])} chars, "
                  f"max_tokens {max_tokens}, thinking cfg {assembled.get('thinking')!r}, utterance {utterance!r}")
            continue
        for rep in range(REPS):
            resp, ms = call(model, key, assembled["system_prompt"], assembled["user_content"], max_tokens)
            text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
            usage = resp.get("usage", {})
            for k_out, k_in in (("in", "input_tokens"), ("out", "output_tokens"),
                                ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens")):
                totals[k_out] += usage.get(k_in, 0) or 0
            totals["calls"] += 1
            raw_obj = parse(text)
            raw_ok, raw_detail = score(raw_obj, expected)
            guarded = guard_response_text(text, req.get("agenda"), req.get("turn_id"),
                                          user_content=utterance, conversation=req.get("conversation"))
            g_obj = parse(guarded)
            g_ok, g_detail = score(g_obj, expected)
            reply = (raw_obj or {}).get("reply")
            reply = reply.get("en") if isinstance(reply, dict) else reply
            row = {"run": run, "rep": rep, "expected": expected, "preregistered": preregistered,
                   "raw_pass": raw_ok, "raw": raw_detail, "guarded_pass": g_ok, "guarded": g_detail,
                   "stop_reason": resp.get("stop_reason"), "ms": ms, "usage": usage,
                   "intent": ((raw_obj or {}).get("intent") or {}), "reply": reply, "text": text}
            rows.append(row)
            print(f"  {run:24} rep{rep} {'PASS' if raw_ok else 'FAIL'} raw | {'PASS' if g_ok else 'FAIL'} guarded | "
                  f"stop={resp.get('stop_reason')} {ms}ms out={usage.get('output_tokens')} | {raw_detail}")
            print(f"  {'':24}      reply: {(reply or '')[:170]}")

    if args.dry:
        return 0
    pre = [r for r in rows if r["preregistered"]]
    gap = [r for r in pre if r["expected"]]
    ctl = [r for r in pre if not r["expected"]]
    extra = [r for r in rows if not r["preregistered"]]
    verdict = sum(r["raw_pass"] for r in gap) == 9 and sum(r["raw_pass"] for r in ctl) == 3
    print("\nPRE-REGISTERED: gap turns %d of %d, phone controls %d of %d, raw output -> %s" % (
        sum(r["raw_pass"] for r in gap), len(gap), sum(r["raw_pass"] for r in ctl), len(ctl),
        "PASS" if verdict else "FAIL"))
    print("after GP's guard: gap %d of %d, controls %d of %d" % (
        sum(r["guarded_pass"] for r in gap), len(gap), sum(r["guarded_pass"] for r in ctl), len(ctl)))
    print("NOT pre-registered, v32-gap-selective-2: %d of %d raw" % (sum(r["raw_pass"] for r in extra), len(extra)))
    print("TOTALS", json.dumps(totals))
    if args.out:
        Path(args.out).write_text(json.dumps({"prompt_version": cfg["version"], "model": model,
                                              "totals": totals, "rows": rows}, ensure_ascii=False, indent=1))
        print("written", args.out)
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
