#!/bin/bash
# Daily memory-mission refresh: backfill all sources, ingest into MemPalace,
# extract entity facts, promote to proposals.
#
# Each source is isolated — one failure does not stop the others. Logs land
# per-source under /var/log/memory-mission/.
#
# Idempotent end-to-end:
#   - StagingWriter.get(external_id) skips already-staged items
#   - MemPalaceAdapter.ingest updates rather than duplicates
#   - extract_pilot skips items with existing extraction reports
#   - promote_staged uses deterministic proposal_id (re-runs return existing)
#
# All operational config (identity, account labels, Composio credentials)
# comes from `deploy/.env.local` — see `deploy/.env.example` for the
# contract. The script aborts loudly if required env is missing.
#
# Required: codex CLI logged in via ChatGPT subscription (NOT API key).
# `codex exec` is what extract_pilot calls per item — API-key mode bills
# metered OpenAI. Subscription is required.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_DIR"

LOG_DIR=/var/log/memory-mission
mkdir -p "$LOG_DIR"
STAMP=$(date -Iseconds)
MAIN_LOG="$LOG_DIR/mm-refresh.log"

echo "[$STAMP] starting mm-refresh (commit $(git rev-parse --short HEAD))" >> "$MAIN_LOG"

# --- env -------------------------------------------------------------------

# Source the deploy-local env (gitignored). This is where MM_USER_ID,
# MM_FIRM_ROOT, MM_*_ACCOUNTS, COMPOSIO_API_KEY etc. live.
if [ -f deploy/.env.local ]; then
  set -a
  . deploy/.env.local
  set +a
else
  echo "[$STAMP] ABORT: deploy/.env.local missing — copy from deploy/.env.example" >> "$MAIN_LOG"
  exit 1
fi

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

PY="$REPO_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "[$STAMP] ABORT: $PY missing — run deploy.sh first" >> "$MAIN_LOG"
  exit 1
fi

# --- helpers ---------------------------------------------------------------

run_step() {
  local label="$1"; shift
  local logfile="$LOG_DIR/$label.log"
  echo "[$(date -Iseconds)] $label start" >> "$MAIN_LOG"
  if "$@" >> "$logfile" 2>&1; then
    echo "[$(date -Iseconds)] $label ok" >> "$MAIN_LOG"
  else
    rc=$?
    echo "[$(date -Iseconds)] $label FAILED (rc=$rc) — see $logfile" >> "$MAIN_LOG"
  fi
}

# Iterate "label:user_id,label:user_id" env entries; emit just the labels.
extract_labels() {
  local raw="${1:-}"
  [ -z "$raw" ] && return
  printf '%s\n' "$raw" | tr ',' '\n' | while IFS= read -r entry; do
    entry="${entry# }"; entry="${entry% }"
    [ -z "$entry" ] && continue
    printf '%s\n' "${entry%%:*}"
  done
}

# --- Phase 1: source backfills (per-account isolation) --------------------

while IFS= read -r label; do
  [ -z "$label" ] && continue
  run_step "cal-$label" "$PY" deploy/scripts/backfill.py calendar "$label"
done < <(extract_labels "${MM_CALENDAR_ACCOUNTS:-}")

while IFS= read -r label; do
  [ -z "$label" ] && continue
  run_step "gmail-$label" "$PY" deploy/scripts/backfill.py gmail "$label"
done < <(extract_labels "${MM_GMAIL_ACCOUNTS:-}")

if [ -n "${MM_GRANOLA_USER_ID:-}" ]; then
  run_step granola "$PY" deploy/scripts/backfill_granola.py
fi

# --- Phase 2: stage → MemPalace -------------------------------------------

run_step mempalace-ingest  "$PY" deploy/scripts/mempalace_ingest.py

# --- Phase 3: extract (codex via subscription) + promote -------------------

run_step extract           "$PY" deploy/scripts/extract_pilot.py
run_step promote           "$PY" deploy/scripts/promote_staged.py

echo "[$(date -Iseconds)] done" >> "$MAIN_LOG"
