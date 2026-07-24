#!/bin/sh
# Weekly prompt-watchdog run (installed in the local crontab; see
# docs/prompt-dossiers/README.md). Compares the working tree's bundle
# and dossiers against the live prod overlay, dials, and 7d wire
# signals, writes a dated report, and raises a macOS notification when
# anything needs attention. Note: the comparison baseline is the local
# checkout, so a mid-edit working tree can show expected drift.
set -u
REPO="/Users/scottguida/cloudzap"
REPORT_DIR="$HOME/ghostpour-watchdog"
mkdir -p "$REPORT_DIR"
OUT="$REPORT_DIR/report-$(date +%Y%m%d-%H%M).md"
cd "$REPO" || exit 1
if "$REPO/.venv/bin/python" ops/prompt_watchdog.py >"$OUT" 2>&1; then
  echo "clean run, report at $OUT"
else
  /usr/bin/osascript -e "display notification \"Findings in $OUT\" with title \"GhostPour prompt watchdog\"" 2>/dev/null
  echo "findings, report at $OUT"
fi
