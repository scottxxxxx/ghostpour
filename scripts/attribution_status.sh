#!/usr/bin/env bash
# Apple Ads attribution: is the exchange sweep working, and are tokens arriving?
#
# WHY THIS FILE EXISTS. This command lived only in chat messages for a week,
# described as "one command on Scott's side", and so nobody ever ran it. The
# row count it produces was the open gate on the 2026-09-15 Apple Search Ads
# campaign start the entire time. A command that is not written down is a
# command that does not exist.
#
#   ./scripts/attribution_status.sh
#
# Read-only. Touches nothing, writes nothing, safe to run any time.
#
# ── HOW TO READ THE OUTPUT ────────────────────────────────────────────────
#
# Two versions of the `attribution_sweep_stalled` alert fired on a healthy
# system on 2026-09-09. Both inferred the sweep's health from something other
# than the sweep's own work: first the age of the oldest pending row, then the
# age of the last successful exchange (which only moves when a NEW token
# arrives, so 14 quiet hours read as 14 dead ones). Since 2026-09-09 the sweep
# stamps `last_attempt_at` on every pending row it gets an answer for, and
# section 3 below prints it. THIS is the discriminator:
#
#   SWEEP ALIVE   every pending row's `last_attempt` is within the last
#                 minute or two. Those rows are tokens Apple has no record
#                 for (404 forever, by design). They expire at 24h and
#                 NOTHING IS LOST, because Apple never had an answer to give.
#                 `last_exchange` being hours old means nothing on its own:
#                 it only moves when an install lands.
#
#   SWEEP DEAD    a pending row older than 15 minutes whose `last_attempt` is
#                 empty or old. Nothing is touching the waiting work. That is
#                 a real gate before any ad spend, because every paid install
#                 would be lost the same way.
#
#   APPLE DOWN    same shape as SWEEP DEAD (a transport failure is not an
#                 attempt), with `adservices exchange transient failure` in
#                 the container log every 60s. Same consequence for tokens.
#
#   NO TOKENS     the table is empty or has only `no_token` rows. Then the
#                 endpoint is live and nothing is posting to it, and the
#                 campaign start should move rather than the plan.
#
# ⚠ `app_version` on the pending rows is the other tell. Debug build numbers
# there mean the tokens came from installs Apple has no record for (not App
# Store, not TestFlight), which is the benign case dressed as the alarming one.
set -euo pipefail

HOST="${GP_HOST:-scottguida@35.239.227.192}"
KEY="${GP_KEY:-$HOME/.ssh/gcp_deploy_key}"

read -r -d '' PYCODE <<'PY' || true
import sqlite3
c = sqlite3.connect("/app/data/cloudzap.db")
c.row_factory = sqlite3.Row

print("=== 1. every row, by status ===")
rows = list(c.execute("""
    SELECT status, app_id, COUNT(*) AS n,
           MIN(created_at) AS oldest, MAX(created_at) AS newest,
           MAX(exchanged_at) AS last_exchange
      FROM ad_attribution
     GROUP BY status, app_id
     ORDER BY n DESC"""))
if not rows:
    print("  (table empty — nothing has ever posted a token)")
for r in rows:
    print(" ", dict(r))

print()
print("=== 2. rows created since 2026-09-07, by status ===")
recent = list(c.execute("""
    SELECT status, COUNT(*) AS n
      FROM ad_attribution
     WHERE created_at >= '2026-09-07'
     GROUP BY status ORDER BY n DESC"""))
if not recent:
    print("  (none — no tokens have arrived since 2026-09-07)")
for r in recent:
    print(" ", dict(r))

print()
print("=== 3. rows still pending: is the sweep touching them? ===")
# `last_attempt` is the sweep's own footprint, stamped on every answer from
# Apple. Within the last minute or two: alive, and the row is a token Apple
# has no record for. Empty or old on a row older than 15 minutes: the sweep
# is not running, or cannot reach Apple. `app_version` is the secondary
# tell: Debug build numbers mean installs Apple never saw.
pend = list(c.execute("""
    SELECT app_version, created_at, last_attempt_at
      FROM ad_attribution WHERE status='pending' AND token IS NOT NULL
     ORDER BY created_at"""))
if not pend:
    print("  (nothing pending — the sweep has cleared everything)")
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
for r in pend:
    d = dict(r)
    la = d.get("last_attempt_at")
    try:
        age = int((now - datetime.fromisoformat(la)).total_seconds()) if la else None
    except ValueError:
        age = None
    d["last_attempt_age_s"] = "never" if age is None else age
    print(" ", d)

print()
# ⚠ The column is `first_seen_at`, not `created_at`. The first real run of
# this script died here on `no such column`, which is the whole argument for
# writing ops commands down and RUNNING them rather than pasting them into a
# message: a query nobody has executed is a query that does not work.
print("=== 4. attribution incidents ===")
inc = list(c.execute("""
    SELECT category, subject, trigger_count, first_seen_at, last_seen_at, resolved_at
      FROM alert_incidents WHERE category LIKE 'attribution%'
     ORDER BY first_seen_at DESC LIMIT 10"""))
if not inc:
    print("  (none)")
for r in inc:
    print(" ", dict(r))
PY

ssh -i "$KEY" "$HOST" 'bash -s' <<EOF
set -e
cat > /tmp/gp_attr_status.py <<'INNER'
$PYCODE
INNER
sudo docker cp /tmp/gp_attr_status.py ghostpour:/tmp/gp_attr_status.py >/dev/null
sudo docker exec ghostpour python /tmp/gp_attr_status.py
EOF
