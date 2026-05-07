# deploy/

Operational artefacts for running memory-mission as a long-lived MCP server
plus a daily ingestion pipeline. Generic — every deploy-specific value comes
from env (`deploy/.env.local`, gitignored). See `.env.example` for the full
contract.

## Layout

```
deploy/
├── individual_with_mempalace.py    # MCP server entry point (stdio)
├── individual_with_mempalace.py    # symlinked at repo root for convenience
├── scripts/
│   ├── _config.py                  # shared env-driven config (imported by all scripts)
│   ├── _kg_projection.py           # KG → generic Person/Company records (target-agnostic)
│   ├── _crm_target.py              # CRMTarget protocol; what every adapter implements
│   ├── _target_hubspot.py          # HubSpotTarget adapter
│   ├── _target_notion.py           # NotionTarget adapter (with --provision)
│   ├── push_to_crm.py              # KG → CRM orchestrator (--target=hubspot|notion)
│   ├── backfill.py                 # Gmail/Calendar → staging via Composio
│   ├── backfill_granola.py         # Granola transcripts → staging
│   ├── composio_live.py            # Composio live-adapter factories (Gmail/Cal/Granola/HubSpot/Notion)
│   ├── mempalace_ingest.py         # staged → MemPalace
│   ├── extract_pilot.py            # staged → ExtractionReport (codex CLI)
│   ├── extract_full.py             # all-staged variant of extract_pilot
│   ├── extract_sample.py           # cost-measuring extraction (Anthropic SDK)
│   ├── cost_benchmark.py           # token/cost projection without LLM calls
│   ├── promote_staged.py           # ExtractionReport → Proposals
│   ├── write_personal.py           # direct personal-KG writes (ADR-0015)
│   ├── migrate_kg_firm_to_personal.py  # one-time firm→personal KG migration
│   └── visualize_kg.py             # interactive HTML KG visualization
├── cron/
│   ├── mm-refresh.sh               # daily pipeline entrypoint (5 phases)
│   ├── install-cron.sh             # idempotent crontab installer
│   └── README.md                   # cron usage + diagnostics
├── .env.example                    # env contract (copy to .env.local on VPS)
└── README.md
```

## Configure first (one-time per deployment)

```
cp deploy/.env.example deploy/.env.local
$EDITOR deploy/.env.local        # fill in MM_USER_ID, MM_FIRM_ROOT, account labels, COMPOSIO_API_KEY, ...
```

`.env.local` is gitignored. The Python scripts read it indirectly (every
script imports `_config.py`, which reads from process env); the cron wrapper
sources it before invoking them.

## MCP server entry point (`individual_with_mempalace.py`)

Wraps `memory_mission.mcp.individual_server` so that `mm_search_recall` is
wired to a real `MemPalaceAdapter` backend. Required env (no defaults —
missing values raise `KeyError`):

| Var | Example | Purpose |
|---|---|---|
| `MM_USER_ID` | `alice` | Employee identity |
| `MM_AGENT_ID` | `hermes` | Consuming agent identity |
| `MM_ROOT` | `/root/memory-mission-data` | Firm root containing `identity.db`, `personal/<user>/...` |

Run via the consuming agent's MCP config (stdio transport). Hermes example:

```yaml
mcp_servers:
  memory_mission:
    command: <repo>/.venv/bin/python
    args: [<repo>/deploy/individual_with_mempalace.py]
    env:
      MM_USER_ID: alice
      MM_AGENT_ID: hermes
      MM_ROOT: /root/memory-mission-data
```

## scripts/

Each script reads identity + paths from env via `_config.py`. Run any of
them with `MM_USER_ID=... MM_FIRM_ROOT=... python deploy/scripts/<name>.py`,
or set env via `deploy/.env.local` and source it before running.

| Script | Purpose | Extra env |
|---|---|---|
| `backfill.py <toolkit> <label>` | Gmail/Calendar → staging | `MM_GMAIL_ACCOUNTS` / `MM_CALENDAR_ACCOUNTS`, `COMPOSIO_API_KEY` |
| `backfill_granola.py` | Granola → staging | `MM_GRANOLA_USER_ID`, `COMPOSIO_API_KEY` |
| `mempalace_ingest.py` | staged → MemPalace | — |
| `extract_pilot.py` | staged → ExtractionReport | codex on subscription |
| `extract_full.py` | all-staged extract | codex on subscription |
| `extract_sample.py` | cost-measuring sample | `ANTHROPIC_API_KEY` |
| `cost_benchmark.py` | offline cost projection | — |
| `promote_staged.py` | reports → proposals | — |
| `write_personal.py` | direct personal-KG writes | — |
| `migrate_kg_firm_to_personal.py` | one-time KG migration | — |
| `visualize_kg.py` | interactive HTML graph | `MM_VIZ_CENTERS` (optional) |

## Deploy contract

VPS pinned to `main` on `SvenWell/memory-mission`. Launcher at
`/root/memory-mission/individual_with_mempalace.py` is a tracked symlink to
`deploy/individual_with_mempalace.py` — Hermes's `mcp_servers` config never
needs to change when the launcher does. Hermes runs as user systemd:
`hermes-gateway.service`.

To deploy a change:

1. Merge into `main` on GitHub.
2. SSH to the VPS, then:

   ```
   cd /root/memory-mission
   ./deploy.sh
   ```

`deploy.sh` is idempotent: fast-forwards `main`, refreshes the editable
install (picks up `pyproject.toml` changes), ensures the launcher symlink,
restarts `hermes-gateway`.

Never edit files on the VPS directly.

### Verify

```
git -C /root/memory-mission rev-parse HEAD          # exact commit
systemctl --user status hermes-gateway              # service health
tail -50 /root/.hermes/logs/mcp-stderr.log          # MCP child log
```

## Daily ingestion

See `deploy/cron/README.md`. Single cron entry runs `deploy.sh` then
`deploy/cron/mm-refresh.sh` to pull latest code, refresh deps, restart
Hermes, and ingest the last day's data across all configured sources.

## KG → CRM projection

`push_to_crm.py` is the single entry point for projecting personal-KG
entities into a CRM. Adapter pattern: each target implements `CRMTarget`
(see `_crm_target.py`); shared filter, dedup, dry-run JSONL preview,
matching, and create/update orchestration live in `push_to_crm.py` +
`_kg_projection.py` once. Adding a new target is dropping in
`_target_<name>.py` and registering it in `_load_target`.

Targets currently shipped:

| Target | Match | Provisioning | Status |
|---|---|---|---|
| `hubspot` | by `email` (contacts) / `domain` (companies) — built-in unique | none (HubSpot has typed objects) | active in cron |
| `notion` | by `mm_entity_id` rich_text (fallback `Email` / `Domain`) | one-time `--provision` creates `Memory Mission CRM` parent + `Contacts (mm)` + `Companies (mm)` databases | active in cron |
| `monday` | (skipped — paid plan blocker; adapter slot reserved) | — | not wired |

Usage:

```bash
# dry-run: writes a JSONL preview of every proposed create/update
python deploy/scripts/push_to_crm.py --target=hubspot
python deploy/scripts/push_to_crm.py --target=notion

# one-time setup (Notion only)
python deploy/scripts/push_to_crm.py --target=notion --provision

# write to the CRM
python deploy/scripts/push_to_crm.py --target=hubspot --apply
python deploy/scripts/push_to_crm.py --target=notion --apply
```

Both targets are idempotent: re-runs match existing records and update
only what's actually different (HubSpot computes a real delta; Notion
re-writes properties unconditionally — no-op when values match).
Previews land at `<MM_FIRM_ROOT>/.crm-preview/<target>-<ts>.jsonl`.

Required env per target lives in `.env.example`. The daily cron checks
each target's env before running it — leave a target unset to skip it.
