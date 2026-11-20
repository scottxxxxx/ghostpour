"""SS live summary + title prompt probe (2026-11-20, Scott's go, cap $1).

Reads cases and prompt variants from a local directory (real transcripts stay
out of the repo), sends them through probe_runner, writes outputs back there.

    python qa/ss_summary_prompt_probe.py --dir <sumtest> --stage summary --reps 3 --dry
    python qa/ss_summary_prompt_probe.py --dir <sumtest> --stage title --dry
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_runner  # noqa: E402
from app.config import get_settings  # noqa: E402

PROD = "claude-haiku-4-5"
TAGS = ("v1", "v3")
ONLY: set = set()


def summary_requests(d: Path, reps: int, models: list[str]) -> list[tuple[str, dict]]:
    tail_en = (d / "tail_en.txt").read_text()
    tail_fr = (tail_en.replace("TRANSCRIPT LANGUAGE: en-GB", "TRANSCRIPT LANGUAGE: fr-FR")
               .replace("Respond in English (en)", "Respond in French (fr)").replace("in English", "in French"))
    cases = json.loads((d / "dev_cases.json").read_text()) + json.loads((d / "holdout_cases.json").read_text())
    out = []
    for model in models:
        for tag in TAGS:
            if model != PROD and tag == "v1":
                continue
            prompts = json.loads((d / f"{tag}.json").read_text())
            for c in [c for c in cases if not ONLY or c["id"] in ONLY]:
                tail = tail_fr if "french" in c["id"].lower() else tail_en
                for r in range(reps):
                    cid = f"{model.split('-')[-2]}{model.split('-')[-1]}_{tag}_{c['id']}_{r}"[:64]
                    out.append((cid, {
                        "model": model, "max_tokens": 4096,
                        "system": prompts[c["mode"]] + tail,
                        "messages": [{"role": "user", "content": c["user"]}],
                    }))
    return out


def loc_requests(d: Path, reps: int) -> list[tuple[str, dict]]:
    """es/fr/ja: native cases, each language's own prompts and production tail."""
    loc = d / "loc"
    cases = json.loads((loc / "loc_cases.json").read_text())
    out = []
    for tag in TAGS:
        for c in cases:
            prompts = json.loads((loc / f"{c['lang']}_{tag}.json").read_text())
            tail = "\n\n" + (loc / f"tail_{c['lang']}.txt").read_text()
            for r in range(reps):
                out.append((f"{tag}_{c['id']}_{r}"[:64], {
                    "model": PROD, "max_tokens": 4096,
                    "system": prompts[c["mode"]] + tail,
                    "messages": [{"role": "user", "content": c["user"]}],
                }))
    return out


def title_requests(d: Path) -> list[tuple[str, dict]]:
    res = json.loads((d / "api_summary_results.json").read_text())
    out = []
    for tag in ("v1", "v2"):
        system = (d / f"title_{tag}.txt").read_text()
        for cid, r in sorted(res.items()):
            if not cid.startswith("45_v3_") or not cid.endswith("_0"):
                continue
            text = r.get("text") or ""
            if len(text.strip()) < 40:
                continue
            out.append((f"t{tag}_{cid}"[:64], {
                "model": PROD, "max_tokens": 100, "temperature": 0.0,
                "system": system, "messages": [{"role": "user", "content": text[:4000]}],
            }))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    probe_runner.add_args(ap)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--stage", choices=("summary", "title", "loc"), required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--also-model", default="")
    ap.add_argument("--tags", default="v1,v3")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    d = Path(args.dir)
    global TAGS, ONLY
    TAGS = tuple(args.tags.split(","))
    ONLY = set(filter(None, args.only.split(",")))
    if args.stage == "summary":
        models = [PROD] + ([args.also_model] if args.also_model else [])
        reqs = summary_requests(d, args.reps, models)
    elif args.stage == "loc":
        reqs = loc_requests(d, args.reps)
    else:
        reqs = title_requests(d)
    results = probe_runner.run(reqs, args, get_settings().anthropic_api_key, Path(__file__).name)
    if results is None:
        return
    flat = {}
    for cid, r in results.items():
        msg = r.get("message") or {}
        text = "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
        flat[cid] = {"text": text, "error": r.get("error"), "usage": msg.get("usage")}
    name = args.out or ("api_summary_results.json" if args.stage == "summary" else "api_title_results.json")
    (d / name).write_text(json.dumps(flat, ensure_ascii=False, indent=1))
    print("wrote", d / name, len(flat))


if __name__ == "__main__":
    main()
