"""Import one version of an app's Help articles into config/help-kb/.

    python scripts/import_help_kb.py --repo ~/ShoulderSurf --commit c992efd3 \
        --expect 64930492bcf3449e...

GP answers Help questions from its own copy of the articles (Scott,
2026-11-27), so its copy has to be the one inside the build that is asking.
The app names its copy by `kb_hash`; this script copies the same files out
of the app's repo at a commit and refuses unless it computes the hash the
app team gave. Every imported version is kept: old builds stay in use.

THE HASH (agreed with SS Dev 2026-11-27; app/services/help_kb.py computes
the same value at load): every index.json and *.md under HelpKnowledgeBase/,
English at the root and one folder per other language. For each file the
line "<path relative to HelpKnowledgeBase>\t<sha256 hex of its bytes>\n",
lines sorted by path in byte order, concatenated, sha256, lowercase hex.
whats-new.json is not part of it.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.services.help_kb import KB_ROOT, is_kb_file, kb_hash_of  # noqa: E402

SOURCE_DIR = "AudioRoutingPrototype/HelpKnowledgeBase"


def _git(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True).stdout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="path to the app's git repo")
    ap.add_argument("--commit", required=True)
    ap.add_argument("--expect", required=True, help="the kb_hash the app team gave")
    ap.add_argument("--app", default="shouldersurf")
    ap.add_argument("--source-dir", default=SOURCE_DIR)
    args = ap.parse_args(argv)

    listing = _git(args.repo, "ls-tree", "-r", "--name-only", args.commit, "--",
                   args.source_dir).decode().splitlines()
    files = {}
    for path in listing:
        rel = path[len(args.source_dir) + 1:]
        if is_kb_file(rel):
            files[rel] = _git(args.repo, "show", f"{args.commit}:{path}")
    got = kb_hash_of(files)
    print(f"{len(files)} files at {args.commit}: {got}")
    if got != args.expect.lower():
        print(f"REFUSING: expected {args.expect}")
        return 2
    out = KB_ROOT / args.app / got
    for rel, data in files.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (out / "SOURCE").write_text(f"commit {args.commit}\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
