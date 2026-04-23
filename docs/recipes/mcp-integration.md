# Recipe: Wire a host agent to Memory Mission via MCP

Memory Mission ships an MCP server at `src/memory_mission/mcp/`. Any MCP-compatible host agent (Claude Code, Cursor, Codex, Hermes, a custom one) can spawn the server as a subprocess and call its 13 tools. This recipe covers setup, registration, and the per-firm security model.

## What you get

Thirteen tools exposed over MCP's stdio transport — 7 read, 6 write. All routed through the same engine + KG + promotion primitives the in-process Python API uses. Every mutating call opens an `observability_scope` so audit trail coverage is complete.

| Tool | Scope | Purpose |
|---|---|---|
| `query` | read | Hybrid search — keyword + vector with permission filter |
| `get_page` | read | One page by slug + plane |
| `search` | read | Keyword search |
| `get_entity` | read | One canonical entity |
| `get_triples` | read | Outgoing / incoming triples for an entity |
| `check_coherence` | read | Non-mutating preview of coherence warnings |
| `compile_agent_context` | read | Distilled context package for a workflow task |
| `create_proposal` | propose | Stage a new proposal for review |
| `list_proposals` | propose | List proposals filtered by status / plane / entity |
| `approve_proposal` | review | Promote — applies facts to the KG |
| `reject_proposal` | review | Reject with rationale |
| `reopen_proposal` | review | Reopen a rejected proposal |
| `merge_entities` | review | Rewrite triples using `source` to use `target` |

Raw SQL access (`KnowledgeGraph.sql_query`) is available as a Python API for admin scripts but NOT exposed over MCP — see "What's deliberately NOT exposed" below.

See `docs/adr/0003-mcp-as-agent-surface.md` for the full rationale.

## Setup (firm operator, one-time)

**1. Lay out the firm directory.**

```
acme/
├── mcp_clients.yaml           ← who can connect + their scopes
├── knowledge.db               ← created on first run
├── proposals.db               ← created on first run
├── identity.db                ← created on first run
├── protocols/
│   └── permissions.md         ← optional, per-employee scopes
├── wiki/
│   ├── firm/<domain>/<slug>.md
│   └── personal/<employee>/<domain>/<slug>.md
└── .observability/            ← created on first run
```

**2. Write `mcp_clients.yaml`.**

```yaml
alice@acme.com:
  scopes: [read, propose, review]

bob@acme.com:
  scopes: [read, propose]

carol@acme.com:
  scopes: [read]
```

Scope meanings:
- `read` — every non-mutating tool (query, get_page, search, get_entity, get_triples, compile_agent_context, check_coherence)
- `propose` — `create_proposal`, `list_proposals`
- `review` — `approve_proposal`, `reject_proposal`, `reopen_proposal`, `merge_entities`

Unknown employees fail closed — the server refuses to start for anyone not in this file.

**3. Install Memory Mission with the MCP dep.**

```bash
pip install -e '.[dev]'   # dev includes mcp runtime dep
```

**4. Smoke-test by running the server directly.**

```bash
python -m memory_mission.mcp \
    --firm-root /path/to/acme \
    --firm-id acme \
    --employee-id alice@acme.com
```

The process blocks on stdio waiting for MCP messages. Ctrl-C to stop. This confirms the manifest + engine bootstrap work before you wire up a host.

## Wire the host agent

### Claude Code

`~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "memory-mission": {
      "command": "python",
      "args": [
        "-m", "memory_mission.mcp",
        "--firm-root", "/path/to/acme",
        "--firm-id", "acme",
        "--employee-id", "alice@acme.com"
      ]
    }
  }
}
```

Restart Claude Code. All 13 tools appear namespaced under `memory-mission:` (e.g., `memory-mission:query`, `memory-mission:approve_proposal`).

### Cursor

`~/.cursor/mcp.json` — same shape as Claude Code above.

### Codex CLI

Codex picks up MCP servers via the global registry on macOS. Drop the same JSON block into `~/.codex/mcp.json`.

### Hermes / custom host

Spawn the server as a subprocess. Communicate via stdio using the MCP protocol — the `mcp` Python SDK provides `stdio_client()` for an in-process client.

## Per-employee deployment

One server process per employee who needs agent access. For a 10-person firm, that's 10 processes (one per host agent running for one employee). Python process overhead is negligible at this scale.

If you want Alice's agent AND Bob's agent running on the same machine, register both servers with different names:

```json
{
  "mcpServers": {
    "memory-mission-alice": {
      "command": "python",
      "args": ["-m", "memory_mission.mcp", "--firm-root", "/path/to/acme",
               "--firm-id", "acme", "--employee-id", "alice@acme.com"]
    },
    "memory-mission-bob": {
      "command": "python",
      "args": ["-m", "memory_mission.mcp", "--firm-root", "/path/to/acme",
               "--firm-id", "acme", "--employee-id", "bob@acme.com"]
    }
  }
}
```

Each host-agent session picks whichever one matches its operator.

## What's deliberately NOT exposed

- **Federated detector** (admin-only, `skills/detect-firm-candidates/`). Runs on the firm's internal admin loop, not via MCP. If you want to expose it later, add an `admin` scope.
- **Direct page writes / `put_page` / `delete_page`.** Firm-plane writes MUST go through `create_proposal` → review gate. No back door.
- **Identity resolver internals** (`resolve()`, `merge_entities` has its own MCP surface though). Raw identity lookups happen at extraction time inside `ingest_facts`; MCP clients work with already-resolved IDs.
- **Observability log direct access.** Audit queries happen outside MCP (operator-only). The stream is append-only JSONL at `firm/.observability/events.jsonl`.

## Troubleshooting

- **"employee not in MCP client manifest"** — add the employee to `firm/mcp_clients.yaml` and restart the server.
- **"missing required scope: review"** — your employee entry doesn't have `review` in its scopes list. If they should approve proposals, add it.
- **"MCP server context not initialized"** — a test or embedding host forgot to call `initialize_from_handles()`. CLI entry always initializes before `mcp.run()`.
- **No pages returned from query** — bootstrap loader couldn't find or parse anything under `firm/wiki/`. Check the path layout (`firm/<domain>/<slug>.md`) and verify `parse_page()` can parse each file standalone.
- **Proposals land but never corroborate** — make sure extraction is emitting `identifiers:` on IdentityFacts so identity canonicalization fires. Without it, the same person appears under different entity names.
