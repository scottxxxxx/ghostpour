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
# The `attribution_sweep_stalled` alert CANNOT distinguish a dead sweep from a
# healthy one whose tokens Apple has no record for. Both leave rows pending and
# both are silent. THIS is the discriminator:
#
#   SWEEP ALIVE   any row has a recent `last_exchange`, or rows sit in
#                 `attributed` / `organic` / `error`. Pending rows are then
#                 tokens Apple has no record for. They will expire at 24h and
#                 NOTHING IS LOST, because Apple never had an answer to give.
#
#   SWEEP DEAD    nothing has EVER been exchanged: no `attributed`, no
#                 `organic`, no `error`, `last_exchange` empty everywhere,
#                 while `pending` grows. That is a real gate before any ad
#                 spend, because every paid install would be lost the same way.
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
print("=== 3. app_version of rows still pending (the debug-build tell) ===")
pend = list(c.execute("""
    SELECT app_version, COUNT(*) AS n, MIN(created_at) AS oldest
      FROM ad_attribution WHERE status='pending'
     GROUP BY app_version ORDER BY n DESC"""))
if not pend:
    print("  (nothing pending — the sweep has cleared everything)")
for r in pend:
    print(" ", dict(r))

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
