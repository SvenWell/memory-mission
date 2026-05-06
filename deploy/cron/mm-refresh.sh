#!/bin/bash
# Daily memory-mission refresh: backfill all sources, ingest into MemPalace,
# extract entity facts, promote to proposals.
#
# Mirrors the gbrain `brain-refresh.sh` pattern: each source is isolated —
# one failure does not stop the others. Logs land per-source for diagnosis.
#
# Idempotent end-to-end:
#   - StagingWriter.get(external_id) skips already-staged items
#   - MemPalaceAdapter.ingest updates rather than duplicates
#   - extract_pilot skips items with existing extraction reports
#   - promote_staged uses deterministic proposal_id (re-runs return existing)
#
# Required: codex CLI logged in via ChatGPT subscription (NOT API key).
# `codex exec` is what extract_pilot calls per item — API-key mode bills
# metered OpenAI. The script aborts loudly if subscription auth isn't active.
#
# Run via cron (see deploy/cron/install-cron.sh) or manually.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_DIR"

LOG_DIR=/var/log/memory-mission
mkdir -p "$LOG_DIR"
STAMP=$(date -Iseconds)
MAIN_LOG="$LOG_DIR/mm-refresh.log"

echo "[$STAMP] starting mm-refresh (commit $(git rev-parse --short HEAD))" >> "$MAIN_LOG"

# --- prerequisites ---------------------------------------------------------

# Codex auth gate. Subscription mode reports "Logged in using ChatGPT".
# API-key mode reports "Logged in using an API key" — bills metered.
codex_status=$(codex login status 2>&1 || true)
if ! echo "$codex_status" | grep -q "ChatGPT"; then
  echo "[$STAMP] ABORT: codex not on subscription auth" >> "$MAIN_LOG"
  echo "[$STAMP]   status: $codex_status" >> "$MAIN_LOG"
  echo "[$STAMP]   fix:    codex login --device-auth, then re-enable cron" >> "$MAIN_LOG"
  exit 1
fi

# Source the gbrain env so Composio / Granola creds are present, mirroring
# the pattern other VPS cron jobs use.
if [ -f /root/.gbrain.env ]; then
  set -a
  . /root/.gbrain.env
  set +a
fi

PY=/root/memory-mission/.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "[$STAMP] ABORT: $PY missing — run deploy.sh first" >> "$MAIN_LOG"
  exit 1
fi

# --- Phase 1: source backfills --------------------------------------------
# Each source has its own log + isolated `|| ... FAILED` so others continue.

run_step() {
  local label="$1"; shift
  local logfile="$LOG_DIR/$label.log"
  echo "[$STAMP] $label start" >> "$MAIN_LOG"
  if "$@" >> "$logfile" 2>&1; then
    echo "[$STAMP] $label ok" >> "$MAIN_LOG"
  else
    rc=$?
    echo "[$STAMP] $label FAILED (rc=$rc) — see $logfile" >> "$MAIN_LOG"
  fi
}

run_step cal-verascient    "$PY" deploy/scripts/backfill.py calendar verascient
run_step cal-purpledorm    "$PY" deploy/scripts/backfill.py calendar purpledorm
run_step gmail-verascient  "$PY" deploy/scripts/backfill.py gmail    verascient
run_step gmail-purpledorm  "$PY" deploy/scripts/backfill.py gmail    purpledorm
run_step granola           "$PY" deploy/scripts/backfill_granola.py

# --- Phase 2: stage → MemPalace -------------------------------------------

run_step mempalace-ingest  "$PY" deploy/scripts/mempalace_ingest.py

# --- Phase 3: extract (codex via subscription) + promote -------------------

run_step extract           "$PY" deploy/scripts/extract_pilot.py
run_step promote           "$PY" deploy/scripts/promote_staged.py

echo "[$(date -Iseconds)] done" >> "$MAIN_LOG"
