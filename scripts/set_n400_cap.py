"""Move the SERVED N-400 monthly cap, and prove the number actually moved.

Runs INSIDE the prod container:

    scp -i ~/.ssh/gcp_deploy_key scripts/set_n400_cap.py \
        scottguida@35.239.227.192:/tmp/
    ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 \
        'docker cp /tmp/set_n400_cap.py ghostpour:/tmp/ && \
         docker exec ghostpour python /tmp/set_n400_cap.py 500 --note "why"'

Reads the admin key from the environment and never prints it. Prints the BEFORE
document, the PUT status, and the AFTER document read back from the running
server, because the receipt for a data change is the data, re-read.

WHY THE READ-BACK IS THE WHOLE POINT
------------------------------------
Scott raised this cap twice through the dashboard, saw a green "Saved! v3" both
times, and NEITHER write reached production: a second copy of the page was open
against a local server, and `BASE = ''` means the page drives whoever served it
(#962). Nothing was broken. The page was never misconfigured, it was A DIFFERENT
PAGE. So a script that reports its own success is worth nothing here; this one
cannot report success without the number having moved on the server it just read.

⚠ THE CAP IS PER USER, not a shared pot. `app_month_spend_usd` sums
WHERE user_id AND app_id. The day `com.weirtech.n400helper` enters
`CZ_APPLE_BUNDLE_ID`, whatever is set here becomes that allowance for EVERY
SIGNUP, with `own_account_meter` keeping it off the shared meter. Lower it first.

⚠ -1 is the documented sentinel for unlimited in `app_budget.flat_cap_usd`.
Setting -1 while the bundle id passes the audience check is the forbidden pair
that #963 refuses calls over. A finite number keeps it impossible by arithmetic
rather than by an errand staying undone.

THE 2026-09-11 RUN, kept as the record
--------------------------------------
Set to 500 on Scott's ruling. Was 20.00, which the QA identity 408a4694 had
exhausted at 19.9815 over 2809 turns, dead since 09-07, so every turn blocked on
the pre-check estimate. Measured rate is $0.007 a turn, so 500 is roughly 70,000
turns a month against the 2809 that exhausted the old cap in ten days. Lifetime
N-400 spend at that point was $21.27, not the $220-$270 that had been reported
upward from tokens times list price with no caching applied.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

BASE = "http://localhost:8000/webhooks/admin/config/n400/budget"


def _key() -> str:
    key = os.environ.get("CZ_ADMIN_KEY", "")
    if not key:
        raise SystemExit("CZ_ADMIN_KEY is empty in this container; refusing to guess")
    return key


def call(method: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE, data=data, method=method,
        headers={"X-Admin-Key": _key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, json.loads(r.read().decode())


def unwrap(doc: dict) -> tuple[dict, bool]:
    wrapped = "data" in doc and isinstance(doc.get("data"), dict)
    return (doc["data"] if wrapped else doc), wrapped


def next_document(doc: dict, cap: float, note: str) -> dict:
    """The full replacement document. PUT is a FULL DOCUMENT REPLACE that
    rejects a body with no `version`, so this copies the whole thing and moves
    two keys; it never sends a partial body, which would succeed and silently
    delete the rest of the document."""
    new = json.loads(json.dumps(doc))
    new["version"] = int(doc.get("version", 1)) + 1
    new["monthly_cost_limit_usd"] = cap
    if note:
        new["_comment"] = list(new.get("_comment") or []) + ["", note]
    return new


def moved(before: dict, after: dict, cap: float) -> bool:
    """Did the number actually move on the server, not merely get sent?"""
    return (after.get("monthly_cost_limit_usd") == cap
            and int(after.get("version", 0)) > int(before.get("version", 0)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cap", type=float,
                    help="the new PER-USER monthly ceiling in USD. -1 is the "
                         "unlimited sentinel and is refused unless --allow-uncapped.")
    ap.add_argument("--note", default="",
                    help="one line appended to the document's _comment saying who "
                         "ruled it and why. Omitted means no comment is appended.")
    ap.add_argument("--allow-uncapped", action="store_true",
                    help="permit -1. Read the forbidden-pair warning first.")
    args = ap.parse_args(argv)

    if args.cap < 0 and not args.allow_uncapped:
        print("REFUSING: %s is the unlimited sentinel. Pass --allow-uncapped only "
              "if the bundle id does NOT pass the audience check." % args.cap)
        return 2

    status, raw = call("GET")
    before, wrapped = unwrap(raw)
    print("BEFORE  status %s  version %s  monthly_cost_limit_usd %r"
          % (status, before.get("version"), before.get("monthly_cost_limit_usd")))

    new = next_document(before, args.cap, args.note)
    status, _ = call("PUT", {"data": new} if wrapped else new)
    print("PUT     status %s" % status)

    status, raw = call("GET")
    after, _ = unwrap(raw)
    print("AFTER   status %s  version %s  monthly_cost_limit_usd %r"
          % (status, after.get("version"), after.get("monthly_cost_limit_usd")))

    ok = moved(before, after, args.cap)
    print("\nRESULT:", "the served cap is %r, read back from the running server" % args.cap
          if ok else "*** THE SERVED VALUE DID NOT MOVE, DO NOT CLOSE THIS OUT ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
