"""Sync one leaf of a served n400 config from the DEPLOYED bundle, then prove it.

Runs INSIDE the prod container:

    scp -i ~/.ssh/gcp_deploy_key scripts/sync_n400_prompt.py \
        scottguida@35.239.227.192:/tmp/
    ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 \
        'docker cp /tmp/sync_n400_prompt.py ghostpour:/tmp/ && \
         docker exec ghostpour python /tmp/sync_n400_prompt.py --expect-sha <merge-commit>'

Reads the admin key from the environment and never prints it.

WHY THIS IS IN THE REPO AND NOT IN /tmp
---------------------------------------
It used to live only in /tmp on two machines, one container recreate away from
the fate of ste_full.json. Worse, the check that matters most was not in it at
all: the operator was expected to confirm the deploy had landed BEFORE running
the sync, and that instruction lived in a chained shell command and in prose.
That is the #948 shape, a command that exists only in chat and is therefore
never run. `--expect-sha` puts the guard inside the thing it guards.

THE TRAP IT EXISTS FOR
----------------------
`sync-from-bundle` copies from the bundle INSIDE THE RUNNING CONTAINER. Run it
before the deploy carrying your change has landed and it pushes the OLD text and
reports success doing it. A sync REPORT and a SERVED VALUE are different events.
So this prints the report, then reads the document back and checks actual
strings.

⚠ `PUT /webhooks/admin/config/{slug}` is a FULL DOCUMENT REPLACE that rejects a
body with no `version`. Never "just PUT systemPrompt": a partial PUT succeeds and
silently deletes the rest of the document. This script never PUTs; it syncs one
leaf pointer, so a dashboard edit to any other key survives.

⚠ PER-VERSION: `PHRASES` and `BLOCK_LINE` below describe ONE prompt version.
They are the v30 (#966) deferral rule and its counterweight. When you ship a new
prompt version, update them to phrases from THAT version, or this reports success
against strings nobody changed. A check that cannot fail is decoration.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SLUG = "n400/interviewer-turn"
ADMIN = "http://localhost:8000/webhooks/admin/config"
HEALTH = "http://localhost:8000/health"

# --- what the served copy must say, for v30 (#966) --------------------------
BLOCK_LINE = 83                      # the DEFERRALS block, 0-indexed
MUST_APPEAR_ONCE = "AND THE CLAUSE MUST NOT BE CONDITIONAL"
PHRASES = [
    "to firm up later IF NEEDED",
    "we can pin the exact day later IF IT MATTERS",
    "to verify the exact days IF NEEDED",
    "Say the return as a fact, not a possibility",
    "if you can find it",                            # the counterweight
    "when you have your mail in front of you",       # the counterweight
    "A condition on WHETHER she has to come back is not",
    "por verificar",                                 # the anchor it follows
    "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM",  # the rule it must not cost
]


def _key() -> str:
    key = os.environ.get("CZ_ADMIN_KEY", "")
    if not key:
        raise SystemExit("CZ_ADMIN_KEY is empty in this container; refusing to guess")
    return key


def call(method: str, url: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Admin-Key": _key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def running_sha() -> str:
    with urllib.request.urlopen(HEALTH, timeout=15) as r:
        return json.loads(r.read().decode()).get("git_sha", "")


def deploy_has_landed(running: str, expected: str) -> tuple[bool, str]:
    """(may we sync, why not).

    Pure so it can be tested without a server. An empty running sha is NOT
    treated as a match: unreachable and unknown are different from equal, the
    same distinction #962 drew for the admin page's build badge.
    """
    if not expected:
        return False, "no --expect-sha given; refusing to sync blind"
    if not running:
        return False, "the running server did not report a git_sha"
    if not (running.startswith(expected) or expected.startswith(running)):
        return False, ("the running image is %s but the change you want is in %s; "
                       "the deploy has not landed, and syncing now would push the "
                       "OLD bundle and report success" % (running[:12], expected[:12]))
    return True, ""


def served() -> tuple[dict, str]:
    _, doc = call("GET", "%s/%s" % (ADMIN, SLUG))
    body = doc.get("data", doc)
    return body, body.get("systemPrompt", "")


def verify(sp: str) -> bool:
    """Read the SERVED string back by phrase. Returns whether it is right."""
    lines = sp.split("\n")
    ok = True

    count = sp.count(MUST_APPEAR_ONCE)
    ok &= count == 1
    print("  %-52s %s" % (MUST_APPEAR_ONCE[:52],
                          "once  OK" if count == 1 else "*** %d ***" % count))

    for phrase in PHRASES:
        present = phrase in sp
        ok &= present
        print("  %-52s %s" % (phrase[:52], "OK" if present else "*** MISSING ***"))

    # Present SOMEWHERE in the document is not the claim; it has to be in the
    # block. Guarded so a shorter prompt reports the wrong section rather than
    # raising IndexError halfway through a verification.
    if len(lines) > BLOCK_LINE:
        in_block = MUST_APPEAR_ONCE in lines[BLOCK_LINE]
        detail = "OK" if in_block else "*** WRONG SECTION ***"
    else:
        in_block, detail = False, "*** PROMPT HAS ONLY %d LINES ***" % len(lines)
    ok &= in_block
    print("  %-52s %s" % ("inside the DEFERRALS block (line %d)" % BLOCK_LINE, detail))
    return bool(ok)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--expect-sha", default="",
                    help="the merge commit carrying the prompt change. The sync "
                         "REFUSES unless the running image is that commit.")
    ap.add_argument("--force", action="store_true",
                    help="sync even if the running image is not --expect-sha. "
                         "Only for re-syncing text already in the running image.")
    args = ap.parse_args(argv)

    running = ""
    try:
        running = running_sha()
    except (urllib.error.URLError, OSError) as exc:
        print("could not read /health: %s" % exc)

    may, why = deploy_has_landed(running, args.expect_sha)
    print("RUNNING %s   EXPECTED %s" % (running[:12] or "unknown",
                                        args.expect_sha[:12] or "none given"))
    if not may:
        if not args.force:
            print("\nREFUSING TO SYNC: %s" % why)
            return 2
        print("\n--force: syncing anyway (%s)" % why)

    body, sp = served()
    print("BEFORE  version=%s  rule_present=%d"
          % (body.get("version"), sp.count(MUST_APPEAR_ONCE)))

    status, rep = call("POST", "%s/%s/sync-from-bundle" % (ADMIN, SLUG),
                       {"keys": ["/systemPrompt"]})
    print("SYNC    HTTP %s  version=%s" % (status, rep.get("version")))
    for c in rep.get("changes", []):
        print("    %-20s %s" % (c.get("key"), c.get("status")))

    body, sp = served()
    print("\nAFTER, read back from the running server by STRING")
    print("  version=%s  systemPrompt chars=%d" % (body.get("version"), len(sp)))
    ok = verify(sp)

    print("\nRESULT:", "the served N-400 prompt carries the rule and its counterweight"
          if ok else "*** THE SERVED PROMPT IS NOT RIGHT, DO NOT CLOSE THIS OUT ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
