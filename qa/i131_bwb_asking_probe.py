"""I-131 opening turn: is `asking` null on a BEFORE WE BEGIN question?

The 2026-10-31 wire proof (I-765 app, i765-helper 0b6469e) got one opening on
prod Sonnet 5 that asked the interpreter question but tagged the standing node
q_p1_rtd_or_reentry, which makes the app read "yes, my cousin interprets" as a
stop answer. Blind subagents on another model never reproduced it (6 of 6 null
on both lanes), so this replays the app's exact opening body on the prod model,
lane v1 (origin/main) against lane v2 (this branch), REPS each.
Prompts come from assemble_prompt plus the en language directive, as chat.py
serves them. Synthetic test install, no user data.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))
import probe_runner  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services.locale_injection import language_directive  # noqa: E402
from app.services.prompt_assembly import assemble_prompt  # noqa: E402

BODY = Path("/Users/scottguida/I765 App/Docs/contracts/wire-proof-i131-2026-10-31/opening-body.json")
REPS = 5
SLUGS = ("i765/interviewer-lane", "i765/form-block-i131")


def configs(rev: str | None) -> dict:
    out = {}
    for slug in SLUGS:
        path = f"config/remote/{slug}.json"
        raw = subprocess.check_output(["git", "show", f"{rev}:{path}"], cwd=ROOT) if rev else (ROOT / path).read_bytes()
        out[slug] = json.loads(raw)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    probe_runner.add_args(ap)
    args = ap.parse_args()
    body = json.loads(BODY.read_text())
    requests = []
    for arm, rev in (("v1", "origin/main"), ("v2", None)):
        cfg = configs(rev)
        p = assemble_prompt("form_interviewer_turn", body["user_content"], cfg, variables=dict(body["metadata"]))
        system = p["system_prompt"] + language_directive("en")
        for r in range(REPS):
            requests.append((f"{arm}-{r}", {
                "model": "claude-sonnet-5", "max_tokens": cfg["i765/interviewer-lane"]["maxTokens"],
                "thinking": {"type": "disabled"},
                "system": system,
                "messages": [{"role": "user", "content": p["user_content"]}],
            }))
    results = probe_runner.run(requests, args, get_settings().anthropic_api_key, Path(__file__).name)
    if not results:
        return
    rows = []
    for cid, _ in requests:
        msg = results.get(cid, {}).get("message")
        text = probe_runner.text_of(msg) if msg else ""
        try:
            env = json.loads(text)
            row = {"id": cid, "asking": env.get("asking"), "facts": env.get("facts"),
                   "reply": (env.get("reply") or {}).get("en")}
        except Exception:
            row = {"id": cid, "parse_error": True, "text": text[:300]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False)[:260])
    out = ROOT / "qa/runs/i131-bwb-asking-probe-2026-10-31.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print("saved", out)


if __name__ == "__main__":
    main()
