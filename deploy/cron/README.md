# deploy/cron/

Daily ingestion automation. Mirrors gbrain's `brain-refresh.sh` pattern.

## What runs daily

Single cron entry at 04:00 UTC:

```
cd /root/memory-mission && ./deploy.sh && ./deploy/cron/mm-refresh.sh
```

Order matters: `deploy.sh` pulls latest main + refreshes deps + restarts
Hermes, *then* `mm-refresh.sh` runs the ingestion pipeline against the
fresh code. If the deploy fails, ingestion does not run that day.

## What `mm-refresh.sh` does

For each Gmail account in `MM_GMAIL_ACCOUNTS`, each Calendar account in
`MM_CALENDAR_ACCOUNTS`, and Granola if `MM_GRANOLA_USER_ID` is set, runs
the full ingest pipeline:

| Step | Script | What |
|---|---|---|
| `cal-<label>` | `backfill.py calendar <label>` | Calendar → staging |
| `gmail-<label>` | `backfill.py gmail <label>` | Gmail → staging |
| `granola` | `backfill_granola.py` | Granola transcripts → staging |
| `mempalace-ingest` | `mempalace_ingest.py` | Staged items → MemPalace |
| `extract` | `extract_pilot.py` | Staged items → ExtractionReport (LLM) |
| `promote` | `promote_staged.py` | ExtractionReport → Proposals |

Each step has its own log under `/var/log/memory-mission/<step>.log` and is
isolated — one failure does not stop the others. All steps are
**idempotent**: re-runs over overlapping windows skip already-processed
items rather than duplicating.

## Required: `deploy/.env.local`

Copy from `deploy/.env.example` and fill in (gitignored). Without it,
`mm-refresh.sh` aborts.

## Required: codex CLI on subscription auth

`extract_pilot.py` calls `codex exec` per item. Codex CLI must be logged in
via ChatGPT subscription, not API key. **API-key mode bills metered OpenAI
spend.** `mm-refresh.sh` aborts loudly if subscription auth isn't active.

```
codex login status            # expected: "Logged in using ChatGPT"
codex login --device-auth     # one-time: switch to subscription
```

## Install

One-shot, on the VPS, after `deploy/.env.local` exists and codex is on
subscription:

```
cd /root/memory-mission
./deploy/cron/install-cron.sh
```

The installer is idempotent — re-running replaces the entry rather than
duplicating it.

## Status / diagnosis

```
# overall summary (last run + step results)
tail -30 /var/log/memory-mission/mm-refresh.log

# per-step detail
tail -50 /var/log/memory-mission/<step>.log

# verify cron is installed
crontab -l | grep -A1 memory-mission

# manually trigger a run any time
cd /root/memory-mission && ./deploy/cron/mm-refresh.sh
```

## Disabling

```
crontab -l | grep -v memory-mission | crontab -
```

The scripts under `deploy/cron/` stay — only the schedule is removed.
