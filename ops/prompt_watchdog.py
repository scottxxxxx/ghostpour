#!/usr/bin/env python3
"""Prompt watchdog: reconcile served rehearsal prompts against intent.

Run from the repo root on a machine holding the prod SSH key:

    python3 ops/prompt_watchdog.py

Checks, per rehearsal call type covered by docs/prompt-dossiers/:

1. Bundle vs overlay content drift (version field excluded). A value
   change merged to the bundle does NOT auto-hydrate; this is the trap
   that keeps a fix from actually serving.
2. Dossier staleness: the dossier's `served_version` vs the live
   overlay version. A stale dossier means an unreconciled prompt
   change (and possibly an eval that never ran).
3. Dial coherence: the model-routing row for the call type exists and
   agrees with the config's `recommendedModel`. A missing row silently
   falls through to the tier default model (how the counterpart lane
   ran on Haiku until 2026-07-23).
4. Wire signals, last 7 days, metadata only (GP retains no rehearsal
   conversation content): models actually used vs the dial, error
   rows, and a parked-scene heuristic on counterpart bursts (several
   consecutive quick turns whose input barely grows).

Exit 0 when clean, 1 when anything needs attention. Output is a
markdown report on stdout.
"""

import json
import pathlib
import re
import subprocess
import sys

SSH_KEY = pathlib.Path.home() / ".ssh" / "gcp_deploy_key"
SSH_HOST = "scottguida@35.239.227.192"
REPO = pathlib.Path(__file__).resolve().parent.parent
DOSSIER_DIR = REPO / "docs" / "prompt-dossiers"
BUNDLE_DIR = REPO / "config" / "remote"

REMOTE_DUMPER = r"""
import glob, json, sqlite3
out = {"configs": {}, "routing": None, "usage": []}
for p in sorted(glob.glob("/app/data/remote-config/techrehearsal/*.json")):
    out["configs"]["techrehearsal/" + p.split("/")[-1][:-5]] = json.load(open(p))
out["routing"] = json.load(open("/app/data/remote-config/model-routing.json"))
db = sqlite3.connect("/app/data/cloudzap.db")
for r in db.execute(
        "SELECT request_timestamp, call_type, model, status, input_tokens, "
        "output_tokens FROM usage_log WHERE call_type LIKE 'tr_%' AND "
        "request_timestamp > datetime('now', '-7 days') "
        "ORDER BY request_timestamp"):
    out["usage"].append(list(r))
print(json.dumps(out))
"""


def remote_dump() -> dict:
    proc = subprocess.run(
        ["ssh", "-i", str(SSH_KEY), SSH_HOST,
         "docker exec -i ghostpour python -"],
        input=REMOTE_DUMPER, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        sys.exit(f"remote dump failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def load_dossiers() -> list[dict]:
    dossiers = []
    for path in sorted(DOSSIER_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text()
        m = re.match(r"---\n(.*?)\n---", text, re.S)
        if not m:
            continue
        fm = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        fm["_file"] = path.name
        dossiers.append(fm)
    return dossiers


def strip_version(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k != "version"}


def main() -> int:
    live = remote_dump()
    dossiers = load_dossiers()
    findings: list[str] = []
    notes: list[str] = []

    # 1 + 2: per-dossier config checks
    for d in dossiers:
        slug = d.get("config_slug", "")
        ct = d.get("call_type", "")
        overlay = live["configs"].get(slug)
        bundle_path = BUNDLE_DIR / (slug + ".json")
        if overlay is None or not bundle_path.exists():
            findings.append(f"{ct}: config missing (overlay={overlay is not None}, "
                            f"bundle={bundle_path.exists()})")
            continue
        bundle = json.loads(bundle_path.read_text())
        if strip_version(bundle) != strip_version(overlay):
            if bundle["version"] > overlay["version"]:
                findings.append(
                    f"{ct}: UNSYNCED bundle change (bundle v{bundle['version']} "
                    f"content differs from overlay v{overlay['version']}) — "
                    f"run sync-from-bundle")
            else:
                findings.append(
                    f"{ct}: overlay content differs from bundle "
                    f"(overlay v{overlay['version']}, bundle v{bundle['version']}) "
                    f"— dashboard/live edit not backported to the repo")
        try:
            recorded = int(d.get("served_version", "-1"))
        except ValueError:
            recorded = -1
        if recorded != overlay["version"]:
            findings.append(
                f"{ct}: dossier {d['_file']} reconciled at v{recorded} but "
                f"overlay serves v{overlay['version']} — reconcile dossier, "
                f"run evals if a graded prompt changed")

        # 3: dial coherence
        rec = overlay.get("recommendedModel")
        row = (live["routing"].get("apps", {}).get("techrehearsal", {})
               .get("call_types", {}).get(ct))
        if rec and row is None:
            findings.append(
                f"{ct}: NO model-routing dial — requests fall to the tier "
                f"default model despite recommendedModel {rec}")
        elif rec and row:
            dialed = set(row.get("models", {}).values())
            if not any(m.endswith(rec) for m in dialed):
                findings.append(
                    f"{ct}: dial {sorted(dialed)} never matches "
                    f"recommendedModel {rec} — deliberate? note it in the dossier")

    # 4: wire signals
    by_ct: dict[str, list] = {}
    for row in live["usage"]:
        by_ct.setdefault(row[1], []).append(row)
    routing_ct = (live["routing"].get("apps", {}).get("techrehearsal", {})
                  .get("call_types", {}))
    for ct, rows in sorted(by_ct.items()):
        models = sorted({r[2] for r in rows if r[2]})
        errors = [r for r in rows if r[3] != "success"]
        notes.append(f"{ct}: {len(rows)} calls, models {models}, "
                     f"{len(errors)} errors")
        dial = routing_ct.get(ct, {}).get("models", {})
        if dial:
            # usage_log records the model without the provider prefix a
            # dial value carries ("perplexity/sonar" vs
            # "openrouter/perplexity/sonar"), so match on suffix.
            def _dialed(m: str) -> bool:
                return any(v == m or v.endswith("/" + m)
                           for v in dial.values())
            stray = [m for m in models if not _dialed(m)]
            if stray:
                findings.append(
                    f"{ct}: served models {stray} outside the dial "
                    f"{sorted(dial.values())} — tier-default fallthrough? "
                    f"(check app_id on those usage rows)")
        if errors:
            findings.append(f"{ct}: {len(errors)} non-success rows in 7d")

    # parked-scene heuristic on counterpart bursts
    cp = by_ct.get("tr_counterpart_turn", [])
    run = 0
    for prev, cur in zip(cp, cp[1:]):
        delta = (cur[4] or 0) - (prev[4] or 0)
        run = run + 1 if 0 <= delta < 60 else 0
        if run >= 3:
            findings.append(
                f"tr_counterpart_turn: possible parked scene around "
                f"{cur[0][:16]} ({run + 1} consecutive turns with input "
                f"deltas under 60 tokens) — pull the session and check")
            run = 0

    print("# Prompt watchdog report\n")
    print("## Signals (7d)\n")
    for n in notes or ["no rehearsal traffic in the window"]:
        print(f"- {n}")
    print("\n## Findings\n")
    if findings:
        for f in findings:
            print(f"- [ ] {f}")
    else:
        print("- none: bundle, overlay, dossiers, dials, and live traffic agree")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
