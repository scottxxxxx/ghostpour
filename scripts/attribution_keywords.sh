#!/usr/bin/env bash
# Apple Ads: installs and purchases per keyword (Social asked 2026-11-21 so
# keywords are judged by purchases, not search popularity).
#
#   ./scripts/attribution_keywords.sh                 # all real campaigns
#   ./scripts/attribution_keywords.sh 2145180693      # one campaign
#   SINCE=2026-12-01 ./scripts/attribution_keywords.sh
#
# Read-only (opens the DB with mode=ro). Prints counts only, no user data.
#
# HOW TO READ IT. One row per campaign / ad group / keyword:
#   installs   attributed installs Apple reported for that keyword
#   signed_in  of those, installs that became a user (only these can be
#              tied to a purchase; the gap is unknowable, not "didn't buy")
#   ever_paid  signed-in users who ever subscribed (users.ever_subscribed)
#   plus/pro   their tier right now (a lapsed subscriber reads free)
# Rows with Apple's placeholder IDs (1234567890 / 12323222, sent when no
# real campaign attributed the install) are counted on one line at the end
# and never mixed into keyword rows.
set -euo pipefail

HOST="${GP_HOST:-scottguida@35.239.227.192}"
KEY="${GP_KEY:-$HOME/.ssh/gcp_deploy_key}"
CAMPAIGN="${1:-}"
SINCE="${SINCE:-2000-01-01}"

read -r -d '' PYCODE <<'PY' || true
import sqlite3, sys
campaign, since = sys.argv[1], sys.argv[2]
c = sqlite3.connect("file:/app/data/cloudzap.db?mode=ro", uri=True)
PLACEHOLDER = 1234567890
where = "a.status='attributed' AND a.created_at >= ?"
args = [since]
if campaign:
    where += " AND a.campaign_id = ?"
    args.append(int(campaign))
rows = c.execute(f"""
    SELECT a.campaign_id, a.ad_group_id, a.keyword_id,
           COUNT(*) AS installs,
           SUM(u.id IS NOT NULL) AS signed_in,
           SUM(COALESCE(u.ever_subscribed, 0) = 1) AS ever_paid,
           SUM(u.tier = 'plus') AS plus,
           SUM(u.tier = 'pro') AS pro,
           MIN(a.created_at), MAX(a.created_at)
      FROM ad_attribution a LEFT JOIN users u ON u.id = a.user_id
     WHERE {where} AND a.campaign_id != {PLACEHOLDER}
     GROUP BY 1, 2, 3 ORDER BY ever_paid DESC, installs DESC""", args).fetchall()
hdr = ("campaign", "ad_group", "keyword", "installs", "signed_in", "ever_paid", "plus", "pro", "first", "last")
print("\t".join(hdr))
for r in rows:
    print("\t".join("" if v is None else str(v) for v in r))
if not rows:
    print("(no real-campaign rows yet)")
ph = c.execute(f"SELECT COUNT(*) FROM ad_attribution a WHERE {where.replace(' AND a.campaign_id = ?','')} AND a.campaign_id = {PLACEHOLDER}", [since]).fetchone()[0]
other = c.execute("SELECT status, COUNT(*) FROM ad_attribution WHERE created_at >= ? AND status != 'attributed' GROUP BY 1", [since]).fetchall()
print(f"\nplaceholder (no real campaign) rows: {ph}")
print("not attributed:", ", ".join(f"{s} {n}" for s, n in other))
PY

ssh -i "$KEY" "$HOST" "sudo docker exec -i ghostpour python - '$CAMPAIGN' '$SINCE'" <<< "$PYCODE"
