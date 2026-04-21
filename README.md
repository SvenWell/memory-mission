# Memory Mission

Enterprise AI knowledge infrastructure for wealth management firms.

The "git repo for knowledge workers" — unified memory that captures what every
employee knows about their clients, aggregates it into firm-level institutional
knowledge, and powers workflows (meeting prep, email drafting, CRM updates)
without the 4 hours/day of admin overhead.

## Architecture at a glance

- **Storage model**: git-backed `.md` files as source of truth (Obsidian-compatible)
  + pgvector as derived retrieval index
- **Runtime**: Hermes Agent (Python-native, built-in learning loop)
- **Integration layer**: Composio MCP Gateway
- **Patterns borrowed**: GBrain (page format, MECE schema, hybrid search),
  MemPalace (temporal knowledge graph), LangChain (middleware + durable execution patterns)

Full architecture and build plan: `/Users/svenwellmann/.claude/plans/gentle-painting-phoenix.md`.
Per-repo analysis: `~/.gstack/projects/SvenWell-memory-mission/repo-analysis/`.

## Getting started

Requires Python 3.12+.

```bash
# Install core + dev dependencies
pip install -e '.[dev]'

# Verify the install
python -m memory_mission --help
python -m memory_mission version
python -m memory_mission info

# Run tests + lint
make test
make lint
make typecheck
```

## Project layout

```
src/memory_mission/
  observability/    Component 0.4 — append-only audit log
  durable/          Component 0.6 — checkpointed execution
  middleware/       Component 0.7 — PII redaction, tool limits, fallback
  memory/           Components 0.1 + 0.2 — employee memory + firm wiki
  ingestion/        Components 1.1-1.3 — backfill, extraction, connectors
  workflows/        Components 2.1-2.4 — meeting prep, email, CRM, interaction
  runtime/          Hermes Agent adapter
  config.py         Pydantic settings
  cli.py            Typer CLI
```

## Progress

See `BUILD_LOG.md` for step-by-step build progress.

## License

Proprietary. Internal use only.
