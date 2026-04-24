---
type: ADR
id: "0005"
title: "SQLite per firm for all persistent state"
status: active
date: 2026-04-24
---

## Context

Memory Mission stores persistent state per firm across several concerns:

- `firm/knowledge.db` — the temporal `KnowledgeGraph` (entities, triples, triple_sources, entity_merges)
- `firm/proposals.db` — the `ProposalStore` (proposal queue + decision history)
- `firm/identity.db` — the `LocalIdentityResolver` (identifiers + stable PersonID / OrgID mappings)
- `firm/mentions.db` — `MentionTracker` (per-entity extraction counts, used by the federated detector)
- `firm/durable.db` — checkpointed execution state for resume-on-crash

Plus a per-firm `firm/.observability/events.jsonl` append-only log that is not an SQLite file but follows the same per-firm-isolation pattern.

Two questions have come up repeatedly:

1. **Should the backend be Postgres instead of SQLite?** The stack had `MM_DATABASE_URL` and an Advisor suggested Postgres at various points. Another Advisor later pushed back: "SQLite instead of Postgres for our per-customer backend database, because it's simpler, the right tool for the job, and it can scale vertically with that customer instance."

2. **Should we use Google Spanner (graph mode) or another hosted graph DB?** A third Advisor surfaced Spanner's new agents-preview + `SpannerGraphQAChain` pattern. The pattern itself is attractive; the platform is not.

## Decision

**SQLite is the default per-firm backend for all persistent state. One firm = one directory = its own SQLite files. No hosted DB pre-pilot. No Postgres. No Spanner. No shared multi-tenant store.**

This formalizes what the code already does (ADR-0002 "two planes, one-way bridge" implied per-firm filesystem isolation; this ADR makes the SQLite choice itself explicit).

## Options considered

- **Option A (chosen): SQLite per firm, per-concern file under `firm/`.** Python stdlib. Zero-dep. WAL + `busy_timeout=5000` enabled on every store so multiple MCP processes per employee coexist safely (see Step 18 security-response commit `7f01c66`). Read-only SQL via `mode=ro` + `PRAGMA query_only=ON` for admin exploration.

- **Option B: Single shared Postgres cluster with row-level security (RLS) for multi-tenancy.** Pro: mature, centrally managed, familiar ops. Con: shared-storage blast radius (an RLS bug is a cross-firm leak), higher ops overhead, blocks the "ship a firm instance as a self-contained directory" story, and kills the per-firm vertical-scale property.

- **Option C: Google Spanner (graph mode) + `SpannerGraphQAChain`.** Pro: graph-native, published LangChain integration, new agents-preview for specialized agent-on-subset patterns. Con: hosted GCP, breaks files-first + per-firm isolation + LLM-with-host invariants. Enterprise pricing doesn't fit 5-20-person pilot firms. The Q&A pattern is portable; the platform isn't (adopted separately as ADR-0006).

- **Option D: PGLite (embedded Postgres).** Pro: familiar Postgres semantics, embeds like SQLite. Con: immature vs. stdlib SQLite, larger footprint, ecosystem churn. Reconsider if SQLite hits a hard limit we can't work around.

- **Option E: DuckDB.** Pro: columnar, fast analytics. Con: we're transactional (promotion writes, corroboration updates, coherence scans), not analytical. Wrong primary use case.

## Rationale

1. **Per-firm isolation IS the security model.** Core rule 6 ("Per-firm isolation — no cross-firm queries") is enforced by the filesystem boundary. SQLite ATTACH is blocked in `sql_query` by keyword rejection (Step 18 fix B7). No shared-storage code path exists to leak across firms.

2. **Vertical scale matches the customer shape.** Small firms (5-20 people), tens-of-thousands of pages / triples, not millions. SQLite on a single VM comfortably handles this range with WAL enabled.

3. **Ops simplicity.** "Ship a firm instance as a tarball of a directory tree" is a real deployment story. Backup is `tar cvf`. Restore is `tar xvf`. Migration is `cp -r`. No cluster to stand up.

4. **Self-host friendly.** Firms that want on-prem or air-gapped deployments get that by default. No "hosted tier" to negotiate.

5. **Obsidian compatibility stays clean.** Pages are markdown on disk; KG is a sibling SQLite file in the same directory. The vault IS the firm instance.

6. **The advisor-confirmed take.** "SQLite is simpler, right tool, scales vertically with the customer." We concurred before the advisor weighed in; their point is alignment, not a new constraint.

## Consequences

- **`MM_DATABASE_URL` stays defined but unused.** Placeholder for a hosted option we would only adopt if a real customer demands it. No code path today references it. README updated accordingly.
- **No cross-firm reporting.** If we ever need "total proposals reviewed across all firms we host," that's an operator-side aggregation over per-firm JSONL / SQLite, not a database query.
- **No RLS / row-level ACL layer to debug.** All access control is Python-level (`can_read`, `can_propose`, `viewer_scopes`). Scope + tier columns on triples (Step 15 + security-response) are also Python-filtered, not DB-enforced.
- **Scale ceiling is the instance.** If a single firm's data grows beyond what SQLite + a single VM handles, we split that firm across multiple instances (unlikely at target segment) or move THAT firm to a hosted backend. Nobody else is forced onto hosted.
- **Concurrent-writer discipline is mandatory.** WAL + `busy_timeout=5000` on every store (`KnowledgeGraph.__init__`, `ProposalStore.__init__`, `LocalIdentityResolver.__init__`). Any new per-firm store added later must follow the same pattern.

## Re-evaluation triggers

- **A pilot demands hosted.** If a real customer-ask requires centralized operations (e.g., their IT won't let us ship a per-firm VM), revisit with that specific deployment model as the design input — not a theoretical one.
- **A single-firm dataset exceeds SQLite comfort range.** If a customer's firm plane grows past ~10 GB of triples / pages AND query latency degrades materially under WAL, revisit (PGLite first; Postgres after).
- **A new store class is added** that inherently needs cross-firm semantics (unlikely — federation happens at the staging + proposal layer, not the store layer).
- **A regulated customer demands at-rest encryption the filesystem can't provide.** SQLCipher or hosted KMS options would be the response.

## Related decisions

- ADR-0002 (Two-plane split) — per-firm isolation at the plane level
- ADR-0003 (MCP as agent surface) — one MCP process per employee, all talking to the same firm's SQLite files via WAL concurrency
- ADR-0006 (pending — Grounded evidence pack pattern) — adopts the Spanner Q&A *interface shape* over our SQLite KG without the Spanner platform
