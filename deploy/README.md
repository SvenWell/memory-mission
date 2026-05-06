# deploy/

Operational artefacts for running memory-mission as a long-lived MCP server.
Captured 2026-05-06 from `/root/memory-mission/` on the production VPS, where
they had been living untracked.

## Layout

```
deploy/
├── individual_with_mempalace.py   # MCP server entry point (genericised; reads MM_USER_ID / MM_AGENT_ID / MM_ROOT)
├── scripts/                        # one-off operational scripts (backfills, migrations, benchmarks)
└── README.md
```

## Entry point

`individual_with_mempalace.py` wraps `memory_mission.mcp.individual_server` so
that `mm_search_recall` is wired to a real `MemPalaceAdapter` backend. Required
env (no defaults — missing values raise `KeyError`):

| Var | Example | Purpose |
|---|---|---|
| `MM_USER_ID` | `keagan` | Employee identity |
| `MM_AGENT_ID` | `hermes` | Consuming agent identity |
| `MM_ROOT` | `/root/memory-mission-data` | Firm root containing `identity.db`, `personal/<user>/...` |

Run via the consuming agent's MCP config (stdio transport).

## scripts/

The 12 backfill / migration / inspection scripts captured from the VPS.
Each currently hardcodes firm/employee/user identity (typically `"keagan"`)
and absolute paths (`/root/memory-mission-data`). They run, but treat them
as VPS-pinned utilities until parameterised. Follow-up: lift constants into
env or argparse so the same scripts work in a local test loop.

| Script | Purpose |
|---|---|
| `backfill.py` | Gmail/Calendar → staging via Composio |
| `backfill_granola.py` | Granola transcripts (last 30d) → staging |
| `composio_live.py` | Composio live-adapter factories |
| `cost_benchmark.py` | Cost/latency benchmarking |
| `extract_full.py` / `extract_pilot.py` / `extract_sample.py` | Extraction pilots over staged content |
| `mempalace_ingest.py` | Ingest staged extractions into MemPalace |
| `migrate_kg_firm_to_personal.py` | One-time KG migration |
| `promote_staged.py` | Promote staging → personal KG |
| `visualize_kg.py` | KG visualization |
| `write_personal.py` | Direct personal-KG writes |

## Deploy contract

VPS is pinned to the `production` branch on `SvenWell/memory-mission`. The
launcher Hermes spawns at `/root/memory-mission/individual_with_mempalace.py`
is a symlink (tracked in git) to `deploy/individual_with_mempalace.py`. So
Hermes's mcp_servers config never has to change when the launcher does.

Hermes runs as a user systemd unit: `hermes-gateway.service`.

To deploy a change to production:

1. Merge into `production` on GitHub (PR or direct push).
2. SSH to the VPS, then:

   ```
   cd /root/memory-mission
   ./deploy.sh
   ```

   `deploy.sh` is idempotent: fast-forwards `production`, ensures the
   launcher symlink, and restarts `hermes-gateway`.

Never edit files on the VPS directly. If you find yourself wanting to,
that change belongs on a branch on GitHub.

### Verifying the running deploy

```
git -C /root/memory-mission rev-parse HEAD          # exact commit
systemctl --user status hermes-gateway              # service health
tail -50 /root/.hermes/logs/mcp-stderr.log          # MCP child log
```
