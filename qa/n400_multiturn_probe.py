"""Multi-turn pre-ship probe for the N-400 interviewer lane: v34 and v35.

    .venv/bin/python qa/n400_multiturn_probe.py --dry        # assemble opening turns only, no API calls
    .venv/bin/python qa/n400_multiturn_probe.py --reps 3     # run both checks, direct API

Ruled by fable-auditor-f5 (2026-09-15): one harness, each check against its own
prompt version, so each probe measures one change.

  v34 (the acknowledgement rule): six plain answers in a row, the exact questions
      from Scott's build 52 screenshot (birth country, nationality, gender, the
      permanent resident date, parent citizen, impairment).
      PASS per rep: no two consecutive replies open with the same word; the replies
      to "Male." and "No." do not echo them; the date is echoed exactly once; all
      six facts are minted.
  v35 (ask the day once): the prior address block.
      PASS per rep: the first reply asks the moved-in day WITH the way out and
      defers nothing; "the 15th" mints p4.prior_address1.from = 2019-01-15 and the
      next reply asks the moved-out day; "I'm not sure" defers
      p4.prior_address1.to with partial 2020-01 and the reply does not ask for a
      day again.

CLIENT LOGIC is the auditor's own harness, `N400 App/qa/n400_qa.py`, imported and
driven unchanged (graph, agenda, known facts, minting, deferrals, cursor). Four
things are replaced and only these: `post` (assembles the prompt locally for the
chosen version and calls the Anthropic API directly, then applies GP's
guard_response_text, the way the route does), `token` (a dummy: no session is
minted), `served_build` (no health read), and `RUNS` (the scratchpad, so nothing
lands in the auditor's qa/runs).

Prompts: v34 is read from the v34 PR branch in git. v35 is v34 with the auditor's
edit (N400 App/contracts/partial-date-ask-once-prompt-v35-edits.md) applied in
memory, the anchor asserted exactly once.

NOT exercised: the route's retry guards (checkpoint refusal, oath modification,
closing while open) and the envelope retry. `thinking` is sent explicitly as
disabled, matching the served config.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
AUDITOR_QA = Path("/Users/scottguida/N400 App/qa")
V35_EDITS = Path("/Users/scottguida/N400 App/contracts/partial-date-ask-once-prompt-v35-edits.md")
V34_REF = "origin/feat/n400-v34-acknowledgement"
SLUG = "n400/interviewer-turn"
CONTEXT = "interpreter: no, filing for self: yes"
PRICE = {"in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.50}  # Sonnet 5, $ per million

V34_TURNS = ["I was born in Monterrey, Mexico.", "Mexico.", "Male.", "January 1st, 2021.", "No.", "No."]
V34_FIELDS = ["p2.country_of_birth", "p2.country_of_nationality", "p2.gender", "p2.lpr_date",
              "p2.parent_citizen_before_18", "p2.disability_exemption"]
V35_TURNS = ["I lived there between January of 2019 to January of 2020, at 335 Gonzales Road, "
             "Santiago, Chile, postal code 12345", "the 15th", "I'm not sure"]

CALLS: list[dict] = []


def git_json(ref: str, path: str) -> dict:
    return json.loads(subprocess.check_output(["git", "-C", str(ROOT), "show", f"{ref}:{path}"]))


def configs_for(version: int) -> dict:
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "--name-only", V34_REF, "config/remote/n400/"]).decode().split()
    out = {"n400/" + Path(n).stem: git_json(V34_REF, n) for n in names if n.endswith(".json")}
    cfg = out[SLUG]
    assert cfg["version"] == 34 and "A PLAIN ANSWER GETS NO ECHO" in cfg["systemPrompt"], "v34 branch is not v34"
    if version == 35:
        md = V35_EDITS.read_text().split("\n")
        old, new = md[17][4:], md[21][4:]
        sp = cfg["systemPrompt"]
        assert sp.count(old) == 1, "v35 anchor not unique in v34"
        cfg = {**cfg, "systemPrompt": sp.replace(old, new), "version": 35}
        assert "the day is ASKED FOR ONCE" in cfg["systemPrompt"]
        out = {**out, SLUG: cfg}
    return out


def make_post(configs: dict, key: str, dry: bool):
    from app.services.n400_envelope import extract_envelope, is_envelope
    from app.services.n400_interviewer_guard import guard_response_text
    from app.services.prompt_assembly import assemble_prompt
    from app.services.spanish_numerals import numeral_variables

    cfg = configs[SLUG]

    def post(body, tok):
        md, utt = body["metadata"], body["user_content"]
        variables = {**md, **numeral_variables(md["call_type"], md.get("locale"), utt)}
        assembled = assemble_prompt(md["call_type"], utt, configs,
                                    jurisdiction=md.get("jurisdiction"), variables=variables)
        assert assembled, "assemble_prompt returned None"
        max_tokens = assembled.get("max_tokens") or cfg.get("maxTokens") or 2048
        if dry:
            print(f"    DRY v{cfg['version']}: system {len(assembled['system_prompt'])} chars, "
                  f"user {len(assembled['user_content'])} chars, max_tokens {max_tokens}, "
                  f"agenda lines {len((md.get('agenda') or '').splitlines())}, utterance {utt!r}")
            raise SystemExit("dry")
        payload = {
            "model": cfg["recommendedModel"], "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "system": [{"type": "text", "text": assembled["system_prompt"], "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": assembled["user_content"]}],
        }
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", "x-api-key": key,
                                              "anthropic-version": "2023-06-01"}, method="POST")
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
        secs = round(time.monotonic() - t0, 1)
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        u = resp.get("usage", {})
        CALLS.append({"version": cfg["version"], "turn_id": md.get("turn_id"), "stop": resp.get("stop_reason"),
                      "secs": secs, "in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                      "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                      "cache_write": u.get("cache_creation_input_tokens", 0) or 0})
        obj_text = text if is_envelope(text) else (extract_envelope(text) or text)
        guarded = guard_response_text(obj_text, md.get("agenda"), md.get("turn_id"),
                                      user_content=utt, conversation=md.get("conversation"))
        return 200, json.dumps({"text": guarded}), secs

    return post


def drive(q, run: str, cursor: str, seed: dict | None, turns: list[str], runs_dir: Path) -> dict:
    seed_path = None
    if seed:
        seed_path = runs_dir / f"{run}.seed.json"
        seed_path.write_text(json.dumps(seed))
    q.cmd_start(Namespace(run=run, lane="interviewer", locale="en", persona=None, context=CONTEXT,
                          volunteer=True, cursor=cursor, seed=str(seed_path) if seed_path else None))
    for utt in turns:
        q.cmd_step(Namespace(run=run, say=utt))
    return json.loads((runs_dir / f"{run}.json").read_text())


def first_word(s: str) -> str:
    m = re.match(r"\W*([A-Za-z']+)", s or "")
    return m.group(1).lower() if m else ""


def score_v34(state: dict) -> dict:
    entries = [e for e in state["transcript"] if e.get("applicant")]
    replies = [e.get("interviewer") or "" for e in entries]
    checks = {}
    if len(replies) < 6 or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(replies)} replies, errors {[e.get('error') for e in entries if e.get('error')]}", "replies": replies}
    openers = [first_word(r) for r in replies]
    checks["openers_vary"] = all(openers[i] != openers[i + 1] for i in range(5))
    checks["no_male_echo"] = not re.search(r"\bmale\b", replies[2], re.I)
    no_echo = lambda r: not re.match(r"\W*(got it,?\s*)?no\b", r, re.I) and "got it, no" not in r.lower()
    checks["no_no_echo"] = no_echo(replies[4]) and no_echo(replies[5])
    date_hits = [len(re.findall(r"January (1st|1|first),? 2021", r, re.I)) for r in replies]
    checks["date_echoed_once"] = date_hits[3] == 1 and sum(date_hits) == 1
    facts = state["facts"]
    checks["all_six_minted"] = all(facts.get(f) not in (None, "") and not (isinstance(facts.get(f), dict) and facts[f].get("deferred")) for f in V34_FIELDS)
    got_it = sum(r.lower().count("got it") for r in replies)
    return {"pass": all(checks.values()), "checks": checks, "openers": openers, "got_it": got_it,
            "date_hits": date_hits, "replies": replies, "facts": {f: facts.get(f) for f in V34_FIELDS}}


def score_v35(state: dict) -> dict:
    entries = [e for e in state["transcript"] if e.get("applicant")]
    if len(entries) < 3 or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(entries)} replies", "replies": [e.get("interviewer") for e in entries]}
    r1, r2, r3 = (e.get("interviewer") or "" for e in entries[:3])
    way_out = lambda r: bool(re.search(r"don.t know|not sure|check (it|that) later|offhand|later", r, re.I))
    asks_day = lambda r: bool(re.search(r"\bday\b|\bdate\b", r, re.I)) and "?" in r
    minted2 = {m.get("field_id"): m.get("value") for m in (entries[1].get("minted") or [])}
    checks = {
        "step1_asks_day_with_way_out": asks_day(r1) and way_out(r1) and bool(re.search(r"move|moved|in\b", r1, re.I)),
        "step1_defers_nothing": not entries[0].get("deferred"),
        "step2_mints_from_2019_01_15": minted2.get("p4.prior_address1.from") == "2019-01-15"
            or state["facts"].get("p4.prior_address1.from") == "2019-01-15",
        "step2_asks_moved_out_day": asks_day(r2) and bool(re.search(r"out|left|leave", r2, re.I)),
        "step3_defers_to": "p4.prior_address1.to" in (entries[2].get("deferred") or []),
        "step3_partial_2020_01": any(d.get("field_id") == "p4.prior_address1.to" and str(d.get("partial_value", "")).startswith("2020-01")
                                     for d in state.get("deferred") or []),
        "step3_does_not_ask_day_again": not re.search(r"what day|which day|exact day", r3, re.I),
    }
    return {"pass": all(checks.values()), "checks": checks, "replies": [r1, r2, r3],
            "minted_step2": minted2, "deferred": state.get("deferred")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", choices=["v34", "v35"])
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    sys.path.insert(0, str(AUDITOR_QA))
    import n400_qa as q
    from app.config import get_settings

    runs_dir = Path("/private/tmp/claude-501/-Users-scottguida-cloudzap/e310dc06-602b-43e8-b44f-dc89a8b6ba0f/scratchpad/qa_runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    q.RUNS = runs_dir
    q.token = lambda: "probe-no-token"
    q.served_build = lambda: {"probe": "direct API, no health read"}

    key = "" if args.dry else get_settings().anthropic_api_key
    if not args.dry:
        assert key, "no Anthropic key in settings"

    plan = []
    if args.only in (None, "v34"):
        plan.append(("v34", 34, "q_p2_country_of_birth", None, V34_TURNS, score_v34))
    if args.only in (None, "v35"):
        plan.append(("v35", 35, "q_p4_prior_address1", {"p4.has_prior_address1": "yes"}, V35_TURNS, score_v35))

    results = []
    stamp = time.strftime("%H%M%S")
    for name, version, cursor, seed, turns, scorer in plan:
        configs = configs_for(version)
        print(f"== {name}: prompt v{configs[SLUG]['version']} {len(configs[SLUG]['systemPrompt'])} chars, cursor {cursor}")
        q.post = make_post(configs, key, args.dry)
        reps = 1 if args.dry else args.reps
        for rep in range(reps):
            run = f"probe-{name}-{stamp}-r{rep}"
            try:
                state = drive(q, run, cursor, seed, turns, runs_dir)
            except SystemExit as e:
                if str(e) == "dry":
                    continue
                raise
            s = scorer(state)
            s.update({"probe": name, "rep": rep, "run": run})
            results.append(s)
            print(f"   {name} rep{rep}: {'PASS' if s['pass'] else 'FAIL'} {s.get('checks') or s.get('why')}")

    if args.dry:
        return 0
    cost = sum(c["in"] * PRICE["in"] + c["out"] * PRICE["out"] + c["cache_read"] * PRICE["cache_read"]
               + c["cache_write"] * PRICE["cache_write"] for c in CALLS) / 1e6
    for name in ("v34", "v35"):
        rs = [r for r in results if r["probe"] == name]
        if rs:
            print(f"{name}: {sum(r['pass'] for r in rs)} of {len(rs)} reps PASS")
    print(f"calls {len(CALLS)}, measured cost ${cost:.2f}, stop reasons {sorted(set(c['stop'] for c in CALLS))}")
    if args.out:
        Path(args.out).write_text(json.dumps({"results": results, "calls": CALLS, "cost_usd": round(cost, 4)},
                                             ensure_ascii=False, indent=1))
        print("written", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
