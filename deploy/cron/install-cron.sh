#!/bin/bash
# Idempotently install the memory-mission daily cron entry.
#
# Adds a single root cron line that:
#   1. Runs deploy.sh (pull main, refresh deps, restart Hermes)
#   2. Runs mm-refresh.sh (backfill + ingest + extract + promote)
#
# Both happen at 04:00 UTC = 06:00 SAST, after gbrain enrichment finishes.
# If deploy.sh fails, mm-refresh.sh does not run (the `&&` chain).
#
# Idempotent: re-running this script replaces any existing memory-mission
# cron entry rather than duplicating it.

set -euo pipefail

ENTRY="0 4 * * * cd /root/memory-mission && ./deploy.sh >> /var/log/memory-mission/deploy.log 2>&1 && ./deploy/cron/mm-refresh.sh"
MARKER="# memory-mission daily refresh"

mkdir -p /var/log/memory-mission

# Read existing crontab, strip any prior memory-mission entry, append fresh one.
existing=$(crontab -l 2>/dev/null || true)
filtered=$(printf '%s\n' "$existing" | awk -v m="$MARKER" '
  $0 == m { skip = 1; next }
  skip == 1 && /memory-mission/ { skip = 0; next }
  { print }
')

{
  printf '%s\n' "$filtered"
  printf '%s\n' "$MARKER"
  printf '%s\n' "$ENTRY"
} | crontab -

echo "Installed. Verify with: crontab -l | grep -A1 'memory-mission'"
echo "First run will be 04:00 UTC tomorrow."
echo "Run manually any time: cd /root/memory-mission && ./deploy/cron/mm-refresh.sh"
