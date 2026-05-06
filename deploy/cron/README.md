# deploy/cron/

Daily ingestion automation for the memory-mission palace running on the VPS.

## What runs daily

Single cron entry at 04:00 UTC (06:00 SAST):

```
cd /root/memory-mission && ./deploy.sh && ./deploy/cron/mm-refresh.sh
```

Order matters: `deploy.sh` pulls latest main + refreshes deps + restarts
Hermes, *then* `mm-refresh.sh` runs the ingestion pipeline against the
fresh code. If the deploy fails, ingestion does not run that day.

## Pipeline `mm-refresh.sh` runs

Each step has its own log under `/var/log/memory-mission/<step>.log` and is
isolated — one failure does not stop the others.

| Step | Script | What |
|---|---|---|
| `cal-verascient` | `backfill.py calendar verascient` | Calendar → staging |
| `cal-purpledorm` | `backfill.py calendar purpledorm` | Calendar → staging |
| `gmail-verascient` | `backfill.py gmail verascient` | Gmail → staging |
| `gmail-purpledorm` | `backfill.py gmail purpledorm` | Gmail → staging |
| `granola` | `backfill_granola.py` | Granola transcripts → staging |
| `mempalace-ingest` | `mempalace_ingest.py` | Staged items → MemPalace |
| `extract` | `extract_pilot.py` | Staged items → ExtractionReport (LLM) |
| `promote` | `promote_staged.py` | ExtractionReport → Proposals |

All steps are **idempotent** — re-runs over overlapping windows skip
already-processed items rather than duplicating.

## Required: codex CLI on subscription auth

`extract_pilot.py` calls `codex exec` per item. Codex CLI must be logged in
via ChatGPT subscription, not API key. **API-key mode bills metered OpenAI
spend.** `mm-refresh.sh` aborts loudly if subscription auth isn't active.

Verify:

```
codex login status
# expected: "Logged in using ChatGPT"
```

Switch to subscription auth (one-time, requires browser):

```
codex login --device-auth
# follow the URL + code prompt
```

## Install

One-shot, on the VPS, after `codex login` shows ChatGPT:

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

Remove the cron entry:

```
crontab -l | grep -v memory-mission | crontab -
```

The scripts under `deploy/cron/` stay — only the schedule is removed.
