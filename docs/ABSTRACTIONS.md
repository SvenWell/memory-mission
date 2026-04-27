# Memory Mission — Abstractions

*Every Pydantic model, every predicate, every tier, every event type in one place. Current as of commit `c5e3a28`-ish (Step 16.5 shipped). For code-level detail, see the module docstrings.*

---

## Design philosophy

Memory Mission's abstractions are **convention-driven but typed**. Every field name and folder path has a well-defined meaning — and every one of them is enforced by a frozen Pydantic model, not a parse-and-pray heuristic. This makes the vault legible to humans, AI agents, and deterministic graders simultaneously.

Three conventions compose:

- **Frozen Pydantic everywhere.** `model_config = ConfigDict(frozen=True, extra="forbid")` on every model that crosses a module boundary. Accidental mutation is blocked; unknown fields raise at construct time.
- **Discriminated unions for variants.** `ExtractedFact`, `Event` — one `kind` or `event_type` field discriminates which shape you get.
- **Protocol + default + adapter slot.** Every external dependency (connector, embedder, resolver) is a runtime-checkable Protocol with a local default implementation and an adapter slot for external services.

See [ARCHITECTURE.md](ARCHITECTURE.md#design-principles) for the complete principles.

---

## Two planes + staging

```
<firm_root>/
├── firm/
│   └── <domain>/<slug>.md
├── personal/
│   └── <employee_id>/
│       ├── working/WORKSPACE.md
│       ├── episodic/AGENT_LEARNINGS.jsonl
│       ├── semantic/<domain>/<slug>.md
│       ├── preferences/PREFERENCES.md
│       └── lessons/{lessons.jsonl, LESSONS.md}
└── staging/
    ├── firm/
    │   └── <source>/<id>.md
    │   └── .facts/<source>/<id>.json
    └── personal/<employee_id>/
        └── <source>/<id>.md
        └── .facts/<source>/<id>.json
```

**Plane** is the fundamental axis: `Literal["personal", "firm"]`. Every write specifies which plane. Staging is the only path between them.

---

## Memory domain model

### Page (`memory/pages.py`)

A curated markdown document — compiled truth + timeline — representing what the firm currently believes about one entity.

```python
class PageFrontmatter(BaseModel):
    slug: str                     # validated: [a-z0-9-], 1-128 chars
    title: str
    domain: str                   # one of CORE_DOMAINS
    aliases: list[str]
    sources: list[str]
    valid_from: date | None
    valid_to: date | None
    confidence: float = 1.0       # 0.0-1.0
    created: datetime | None
    updated: datetime | None
    tier: Tier = "decision"       # Step 15
    reviewed_at: datetime | None = None  # Move 2 (polish): reviewer sign-off timestamp
    # extra="allow" preserves unknown fields on round-trip
```

`reviewed_at` drives the Obsidian Bases dashboard's "Stale or unreviewed" view. Left `None` until a reviewer (or a reviewer skill after approving a proposal) explicitly sets it. Automatic back-stamping defeats the purpose.

```python
class Page(BaseModel):
    frontmatter: PageFrontmatter
    compiled_truth: str = ""      # body above the --- zone separator
    timeline: list[TimelineEntry]  # newest-first below the separator
```

```python
class TimelineEntry(BaseModel):
    entry_date: date
    source_id: str
    text: str
```

**Render format:**

```markdown
---
slug: sarah-chen
title: Sarah Chen
domain: people
tier: policy
confidence: 0.95
---

Sarah is the CEO of [[acme-corp]]. Prefers direct, numbers-heavy
communication.

---

2026-04-15 [interaction-2]: Confirmed CEO role in board meeting
2026-04-10 [interaction-1]: First mention as "CEO of Acme"
```

### Domain registry (`memory/schema.py`)

```python
CORE_DOMAINS: tuple[str, ...] = (
    "people", "companies", "deals", "meetings",
    "concepts", "sources", "inbox", "archive",
)
```

Domain comes from the `domain:` frontmatter field, never inferred from folder position. Paths follow `<plane_root>/semantic/<domain>/<slug>.md` (personal) or `firm/<domain>/<slug>.md` (firm). Validated via `validate_domain()`.

### Tiers (`memory/tiers.py`)

```python
Tier = Literal["constitution", "doctrine", "policy", "decision"]
DEFAULT_TIER: Tier = "decision"
```

Ordinal authority: `constitution > doctrine > policy > decision`. Default is `decision` — everyday facts. Higher tiers require deliberate editorial acts. Helpers: `tier_level(t) -> int`, `is_above(a, b) -> bool`, `is_at_least(a, floor) -> bool`.

| Tier | Meaning | Example |
|---|---|---|
| `constitution` | Hardest-to-amend truths | "Firm mission: preserve client capital" |
| `doctrine` | Canonical operating beliefs | "We buy durable compounders" |
| `policy` | Operational rules | "Review allocations quarterly" |
| `decision` | Specific observed facts (default) | "Sarah mentioned the Q3 offsite" |

---

## Knowledge graph domain model (`memory/knowledge_graph.py`)

### Entity

```python
class Entity(BaseModel):
    name: str                     # stable ID or kebab-case label
    entity_type: str = "unknown"  # "person", "company", "unknown", ...
    properties: dict[str, Any]    # free-form
```

Post-Step-14, `name` is typically a resolver-issued stable ID: `p_<token>` for persons, `o_<token>` for organizations.

### Triple

```python
class Triple(BaseModel):
    subject: str
    predicate: str
    object: str
    valid_from: date | None
    valid_to: date | None             # None = currently true
    confidence: float = 1.0           # 0.0-1.0, capped at 0.99 via corroborate()
    source_closet: str | None         # "firm" or "personal/<emp>"
    source_file: str | None           # path to the source report
    corroboration_count: int = 0      # Step 13: times re-extracted
    tier: Tier = "decision"           # Step 15
```

**Invariants:**
- Append-only. Never deleted, only invalidated (`valid_to = <date>`).
- `is_valid_at(as_of)` method honors both sides of the window; `valid_to` is **exclusive** ("ended on" means already over by that day).
- `corroboration_count >= 0`.

### TripleSource (Step 13)

```python
class TripleSource(BaseModel):
    source_closet: str | None
    source_file: str | None
    confidence_after: float           # triple's confidence after this corroboration
    added_at: datetime
```

Every `add_triple` seeds one row. Every `corroborate` appends one. The chain is the provenance history for the (subject, predicate, object) currently-true triple.

### MergeResult (Step 14b)

```python
class MergeResult(BaseModel):
    source_entity: str
    target_entity: str
    reviewer_id: str
    rationale: str
    merged_at: datetime
    triples_rewritten: int
```

Audit record for every `merge_entities` call. Queryable via `kg.merge_history(entity_name)` — returns every merge where the entity appears as source or target, oldest-first.

### CoherenceWarning (Step 15b)

```python
class CoherenceWarning(BaseModel):
    subject: str
    predicate: str
    new_object: str
    new_tier: Tier
    conflicting_object: str
    conflicting_tier: Tier
    conflict_type: Literal["same_predicate_different_object"]
    # computed: higher_tier, lower_tier
```

Emitted by `kg.check_coherence(subject, predicate, obj, *, new_tier)`. V1 detects only `same_predicate_different_object` — a new fact with a different object on a subject-predicate pair that already has a currently-true row. Open to extension.

### GraphStats

```python
class GraphStats(BaseModel):
    entity_count: int
    triple_count: int
    currently_true_triple_count: int
```

### Knowledge graph operations

| Method | What it does |
|---|---|
| `add_entity(name, *, entity_type, properties)` | Idempotent upsert. |
| `get_entity(name) -> Entity | None` | Lookup by name. |
| `add_triple(s, p, o, *, valid_from, valid_to, confidence, source_closet, source_file, tier)` | Append a triple + seed `triple_sources`. |
| `find_current_triple(s, p, o) -> Triple | None` | Step 13 helper for the promotion pipeline. |
| `corroborate(s, p, o, *, confidence, source_closet, source_file) -> Triple | None` | Noisy-OR update, capped at 0.99, appends to `triple_sources`. Returns `None` if no currently-true match. |
| `invalidate(s, p, o, *, ended) -> int` | Set `valid_to` on matching currently-true triples. |
| `merge_entities(source, target, *, reviewer_id, rationale) -> MergeResult` | Rewrite triples, delete source entity row, audit the event. |
| `merge_history(name) -> list[MergeResult]` | Audit trail. |
| `check_coherence(s, p, o, *, new_tier) -> list[CoherenceWarning]` | Deterministic conflict detector. Empty list = no conflict. |
| `triple_sources(s, p, o) -> list[TripleSource]` | Full provenance history, oldest-first. |
| `scan_triple_sources(*, closet_prefix, currently_true_only)` | The join the federated detector needs, returned as list of dicts. |
| `sql_query(query, params, *, row_limit) -> list[dict]` | Step 16.5 — read-only SQL over the KG's tables. Engine-enforced read-only. |

### `BrainEngine` read-path signatures (Move 5 polish)

`get_page` / `search` / `query` accept optional `viewer_id: str | None` + `policy: Policy | None`. When both are supplied, results are filtered through `can_read(policy, viewer_id, page)` for firm-plane pages; personal-plane pages are dropped unless the viewer owns them. When either is `None`, no filtering is applied — this keeps every existing caller backwards-compatible and requires callers who want enforcement to opt in explicitly.

```python
engine.query(
    "quarterly review",
    viewer_id="alice",
    policy=firm_policy,
    tier_floor="policy",
)  # returns only pages Alice can read, at or above policy tier
```
| `query_entity(name, *, direction, as_of) -> list[Triple]` | "Everything involving this entity." |
| `query_relationship(predicate, *, as_of) -> list[Triple]` | "Everything with this predicate." |
| `timeline(entity_name) -> list[Triple]` | Chronological. |
| `stats() -> GraphStats` | Shape snapshot. |

### Canonical predicate vocabulary

The promotion pipeline writes these predicates from extracted facts. New predicates can land — the KG is content-agnostic — but downstream tools assume the canonical ones:

| Predicate | Source fact type | Shape |
|---|---|---|
| `<custom>` | `RelationshipFact` | Any LLM-emitted predicate (e.g., `works_at`, `reports_to`, `knows`) |
| `prefers` | `PreferenceFact` | Subject + preference string as object |
| `event` | `EventFact` | Subject + description as object, `valid_from = event_date` |
| `<custom>` | `UpdateFact.predicate` | Same as RelationshipFact, with `invalidate` fired first if `supersedes_object` set |

IdentityFact and OpenQuestion never write predicates.

---

## Extraction domain model (`extraction/schema.py`)

### ExtractedFact — discriminated union

```python
class _FactBase(BaseModel):
    confidence: float        # 0.0-1.0
    support_quote: str       # non-empty — "no quote, no fact"
```

```python
class IdentityFact(_FactBase):
    kind: Literal["identity"]
    entity_name: str
    entity_type: str = "unknown"
    properties: dict[str, Any]
    identifiers: list[str]    # Step 14c — "email:x", "linkedin:y"
```

```python
class RelationshipFact(_FactBase):
    kind: Literal["relationship"]
    subject: str
    predicate: str
    object: str
```

```python
class PreferenceFact(_FactBase):
    kind: Literal["preference"]
    subject: str
    preference: str
```

```python
class EventFact(_FactBase):
    kind: Literal["event"]
    entity_name: str
    event_date: date | None
    description: str
```

```python
class UpdateFact(_FactBase):
    kind: Literal["update"]
    subject: str
    predicate: str
    new_object: str
    supersedes_object: str | None   # invalidates this currently-true triple first
    effective_date: date | None
```

```python
class OpenQuestion(_FactBase):
    kind: Literal["open_question"]
    question: str
    hypothesis: str | None
    # Never promotes — routes to human review instead
```

### ExtractionReport

```python
class ExtractionReport(BaseModel):
    source: str
    source_id: str
    target_plane: Plane
    employee_id: str | None         # required if target_plane == "personal"
    extracted_at: datetime
    facts: list[ExtractedFact]

    def entity_names(self) -> list[str]: ...
```

Written to fact staging at `<wiki_root>/staging/<plane>/.facts/<source>/<source_id>.json`.

---

## Identity domain model (`identity/base.py`)

```python
EntityKind = Literal["person", "organization"]

class Identity(BaseModel):
    id: str                       # "p_<token>" or "o_<token>"
    entity_type: EntityKind
    canonical_name: str | None
    created_at: datetime
```

### IdentityResolver Protocol

```python
@runtime_checkable
class IdentityResolver(Protocol):
    def resolve(
        self,
        identifiers: set[str],
        *,
        entity_type: EntityKind = "person",
        canonical_name: str | None = None,
    ) -> str: ...
    def lookup(self, identifier: str) -> str | None: ...
    def bindings(self, identity_id: str) -> list[str]: ...
    def get_identity(self, identity_id: str) -> Identity | None: ...
```

### Identifier format

Strings of the form `"type:value"`. Types are free-form so adapters can extend: `email:alice@acme.com`, `linkedin:alice-s`, `twitter:@alice`, `phone:+1234567890`, `domain:acme.com`, `name:Alice Smith`.

### `IdentityConflictError`

Raised when `resolve()` receives identifiers that map to multiple existing identities. Carries `identifiers` and `matched_ids` for the caller to route to `merge_entities`.

---

## Promotion domain model (`promotion/proposals.py`)

### Proposal

```python
ProposalStatus = Literal["pending", "approved", "rejected"]

class DecisionEntry(BaseModel):
    decision: Literal["approved", "rejected", "reopened"]
    reviewer_id: str
    rationale: str                # non-empty — rubber-stamping blocked
    at: datetime

class Proposal(BaseModel):
    proposal_id: str              # deterministic hash — re-extraction is idempotent
    target_plane: Plane
    target_employee_id: str | None
    target_scope: str = "public"
    target_entity: str
    proposer_agent_id: str
    proposer_employee_id: str
    facts: list[ExtractedFact]
    source_report_path: str       # "federated-detector://..." for Step 16 origin
    status: ProposalStatus = "pending"
    reviewer_id: str | None
    rationale: str | None
    decided_at: datetime | None
    rejection_count: int = 0
    decision_history: list[DecisionEntry]
```

### Pipeline operations

| Function | Effect |
|---|---|
| `create_proposal(store, ...)` | Stage a pending proposal. Idempotent on `proposal_id`. Logs `ProposalCreatedEvent`. |
| `promote(store, kg, id, *, reviewer_id, rationale, policy=None)` | Apply facts atomically. Runs coherence scan; emits `CoherenceWarningEvent`s; raises `CoherenceBlockedError` in strict mode. Logs `ProposalDecidedEvent(decision="approved")`. |
| `reject(store, id, *, reviewer_id, rationale)` | Mark rejected. `rejection_count++`. |
| `reopen(store, id, *, reviewer_id, rationale)` | Rejected → pending. |

---

## Permissions (`permissions/policy.py`)

```python
class Scope(BaseModel):
    name: str
    description: str = ""
    parent: str | None = None

class EmployeeEntry(BaseModel):
    employee_id: str
    scopes: frozenset[str]

class Policy(BaseModel):
    firm_id: str
    scopes: dict[str, Scope]
    employees: dict[str, EmployeeEntry]
    default_scope: str = "public"
    constitutional_mode: bool = False  # Step 15b
```

Rules: `can_read(policy, employee_id, page)` — default deny; public always allowed; unknown scope fails closed. `can_propose(policy, employee_id, target_scope)` — no-escalation: must have read access to the target scope.

---

## Federated detector domain model (`federated/detector.py`)

```python
class CandidateSource(BaseModel):
    source_closet: str            # "personal/<employee_id>"
    source_file: str
    triple_id: int
    confidence: float

class FirmCandidate(BaseModel):
    subject: str
    predicate: str
    object: str
    tier: Tier = "decision"
    distinct_employees: int
    distinct_source_files: int
    contributing_sources: list[CandidateSource]
    confidence: float             # the triple's current corroborated value

    @property
    def employee_ids(self) -> list[str]: ...
    def to_relationship_fact(self) -> RelationshipFact: ...
```

Returned by `detect_firm_candidates(kg, *, min_employees=3, min_sources=3)`. Thresholded on BOTH counts to defeat the shared-source failure mode.

---

## Synthesis domain model (`synthesis/context.py`)

Output of `compile_agent_context` — the V1 workflow-level primitive. Structured Pydantic tree so the eval harness can grade it without parsing prose; `.render()` produces markdown for the host-agent LLM.

### AttendeeContext

```python
class AttendeeContext(BaseModel):
    attendee_id: str                              # stable ID or raw name
    canonical_name: str | None = None             # filled by IdentityResolver
    outgoing_triples: list[Triple]                # non-event, non-preference
    incoming_triples: list[Triple]                # inverse relationships
    events: list[Triple]                          # predicate == "event"
    preferences: list[Triple]                     # predicate == "prefers"
    related_pages: list[Page]                     # curated pages matching slug
    coherence_warnings: list[CoherenceWarning]    # Move 3 — via observability log

    @property
    def fact_count(self) -> int: ...
    @property
    def display_name(self) -> str: ...
```

Shape mirrors Tolaria's Neighborhood mode (ADR-0069): outgoing / incoming / events / preferences as their own groups, empty groups visible so the LLM sees absence explicitly.

### DoctrineContext

```python
class DoctrineContext(BaseModel):
    pages: list[Page]                             # filtered by tier_floor
    # Sorted highest-tier first, alphabetical within tier
```

Empty when `tier_floor` is `None` or no engine was supplied. The caller explicitly opts in by passing both.

### AgentContext

```python
class AgentContext(BaseModel):
    role: str                                     # "meeting-prep", "email-draft", ...
    task: str
    plane: Plane
    as_of: date | None                            # time-travel
    tier_floor: Tier | None
    attendees: list[AttendeeContext]
    doctrine: DoctrineContext
    generated_at: datetime

    @property
    def fact_count(self) -> int: ...              # sum across attendees
    @property
    def attendee_ids(self) -> list[str]: ...

    def render(self) -> str: ...                  # markdown with [!contradiction] callouts
```

Produced by `compile_agent_context(role, task, attendees, kg, *, engine=None, plane="firm", employee_id=None, tier_floor=None, as_of=None, identity_resolver=None)`. Read-only, single-pass, idempotent (modulo `generated_at` timestamp).

---

## Observability (`observability/events.py`)

All events share:

```python
class _EventBase(BaseModel):
    schema_version: int = 1
    event_id: UUID
    timestamp: datetime
    firm_id: str
    employee_id: str | None
    trace_id: UUID | None
```

Event types (discriminated by `event_type`):

| Event | Fired by | Carries |
|---|---|---|
| `ExtractionEvent` | `ingest_facts` (via host) | source, facts, confidence, llm_provider, prompt_hash |
| `PromotionEvent` | legacy — kept for compat | candidate fact, scores, gates, decision, reviewer |
| `RetrievalEvent` | `BrainEngine.search/query` | query, tier, pages_loaded, token_budget, latency |
| `DraftEvent` | workflow agents | workflow, context_pages, output_preview |
| `ConnectorInvocationEvent` | `connectors.invoke` | connector_name, action, preview (PII-scrubbed), latency |
| `ProposalCreatedEvent` | `create_proposal` | proposal_id, target_plane, fact_count, source_report_path |
| `ProposalDecidedEvent` | `promote` / `reject` / `reopen` | proposal_id, decision, reviewer_id, rationale |
| `CoherenceWarningEvent` | `_apply_facts` coherence scan (Step 15b) | proposal_id, subject, predicate, new_object, new_tier, conflicting_object, conflicting_tier, `blocked: bool` |

### Query helper (Move 3 polish)

```python
coherence_warnings_for(entity_id: str, *, since: datetime | None = None) -> list[CoherenceWarningEvent]
```

Reads the active firm's append-only JSONL via `current_logger` and returns unresolved `CoherenceWarningEvent`s where `subject == entity_id`. Used by `render_page()` and `compile_agent_context` to emit `> [!contradiction]` callouts above entities with outstanding conflicts. V1 treats every warning as unresolved — a future `CoherenceResolvedEvent` will let the helper filter resolved pairs automatically.

---

## Ingestion + connector envelope (`ingestion/`)

Capability-based binding between logical roles and concrete apps,
plus the single envelope shape every connector emits before staging.
ADR-0007 + ADR-0011.

### `ConnectorRole` (`ingestion/roles.py`)

```python
class ConnectorRole(StrEnum):
    EMAIL = "email"
    CALENDAR = "calendar"
    TRANSCRIPT = "transcript"
    DOCUMENT = "document"
    WORKSPACE = "workspace"
    CHAT = "chat"
```

Six logical capabilities. A firm's `firm/systems.yaml` binds each role
to a concrete app. The same app can fulfil multiple roles (Notion as
both `document` and `workspace`).

### `NormalizedSourceItem` (`ingestion/roles.py`)

The single envelope every connector emits before staging. Frozen
Pydantic, `extra="forbid"`. Fields: `source_role: ConnectorRole`,
`concrete_app: str`, `external_object_type: str`, `external_id: str`,
`container_id: str | None`, `url: str | None`, `modified_at: datetime`,
`visibility_metadata: dict[str, Any]`, `target_scope: str`,
`target_plane: Plane`, `title: str`, `body: str`, `raw: dict[str, Any]`.

`target_scope` and `target_plane` come from the firm's `SystemsManifest`,
not from the connector. `visibility_metadata` is per-app (each envelope
helper extracts the visibility surface from raw before mapping).

### `SystemsManifest` + `RoleBinding` + `VisibilityRule` (`ingestion/systems_manifest.py`)

```python
class VisibilityRule(BaseModel):
    if_label: str | None      # matches if metadata["labels"] contains this
    if_field: dict[str, Any]  # matches if every key=value pair equals metadata[key]
    scope: str                # the firm scope to assign on match

class RoleBinding(BaseModel):
    app: str
    target_plane: Plane
    visibility_rules: tuple[VisibilityRule, ...]
    default_visibility: str | None  # None = fail-closed; str = operator-set fallback

class SystemsManifest(BaseModel):
    firm_id: str
    bindings: dict[ConnectorRole, RoleBinding]
```

`load_systems_manifest(path)` parses `firm/systems.yaml`.
`map_visibility(metadata, *, role, manifest) -> str` evaluates rules in
order (first match wins), falls back to `default_visibility`, raises
`VisibilityMappingError` if neither matches.

### Per-app envelope helpers (`ingestion/envelopes.py`)

Pure functions: `(raw_payload, *, manifest, [extra]) -> NormalizedSourceItem`.

| Helper | Role | Concrete app | Notes |
|---|---|---|---|
| `gmail_message_to_envelope` | email | gmail | labels + recipients surface |
| `outlook_message_to_envelope` | email | outlook | sensitivity field surfaces as `outlook_sensitivity` for `if_field` |
| `granola_transcript_to_envelope` | transcript | granola | attendees surface |
| `calendar_event_to_envelope` | calendar | gcal | `gcal_visibility` field surfaces |
| `drive_file_to_envelope` | document | drive | synthesizes `drive_anyone` from permissions |
| `onedrive_item_to_envelope` | document | one_drive | covers SharePoint document libraries; synthesizes `drive_anyone`, `drive_organization_link`, `is_sharepoint`, `sharepoint_site_id` |
| `affinity_record_to_envelope` | workspace | affinity | takes `object_type` ∈ `{organization, person, opportunity}`; surfaces list-membership as `list:<id>` labels + `global` flag |
| `attio_record_to_envelope` | workspace | attio | takes `object_slug` (system or custom); surfaces list-membership as labels + `attio_object_slug` field |
| `notion_page_to_envelope` | workspace | notion | handles pages + database rows; reads pre-flattened `raw["block_content"]` for body |
| `slack_message_to_envelope` | chat | slack | per-message envelope; **structurally overrides `target_plane` to `personal` when `is_im` or `is_mpim`** (ADR-0011) |

Each helper checks the manifest binding's `app` matches its expected
app and raises `ValueError` on mismatch (caller used the wrong helper).

### `StagingWriter` envelope path (`ingestion/staging.py`)

`write_envelope(item: NormalizedSourceItem) -> StagedItem` — validates
`item.target_plane == self._target_plane` and `item.concrete_app ==
self._source` (raises `ValueError` on mismatch), derives `item_id` from
`external_id`, atomically writes raw JSON sidecar + frontmatter-headed
markdown. Frontmatter includes `target_scope`, `source_role`,
`external_object_type`, `container_id`, `url`, `modified_at` from the
envelope.

The pre-existing `write(item_id, raw, markdown_body, ...)` method stays
for ad-hoc / non-envelope writes (Composio invocation logs, free-form).

### `VisibilityMappingError`

`ValueError` subclass raised by `map_visibility` when no rule matches
and no `default_visibility` is set. Envelope helpers propagate this —
backfill skills are required to **stop and surface** rather than retry
with a different scope.

---

## Coverage primitives (`synthesis/coverage.py`)

Context-farming aggregate functions (ADR-0012). Five named primitives
the operator uses to detect what the brain needs from the farmer.

### Aggregate models (Pydantic, frozen)

| Model | What it represents |
|---|---|
| `DomainCoverage` | Page count per domain, broken down by tier |
| `DecayedPage` | Doctrine+ page untouched in N+ days |
| `MissingPageCoverage` | Entity referenced by N+ triples or N+ proposals without an existing doctrine page |
| `AttributionDebt` | Currently-true triple missing `source_closet` or `source_file` |
| `LowCorroborationCluster` | Entity with N+ currently-true triples below confidence floor |

### Functions

```python
compute_domain_coverage(engine, *, plane=None, employee_id=None) -> list[DomainCoverage]
find_decayed_pages(engine, *, plane=None, employee_id=None,
                   min_age_days=90, min_tier="doctrine", now=None) -> list[DecayedPage]
find_missing_page_coverage(engine, kg, store=None, *, plane=None, employee_id=None,
                           min_triple_mentions=3, min_proposal_mentions=0) -> list[MissingPageCoverage]
find_attribution_debt(kg) -> list[AttributionDebt]
find_low_corroboration_clusters(kg, *, confidence_floor=0.7,
                                min_cluster_size=3) -> list[LowCorroborationCluster]
```

All pure functions over the existing `BrainEngine` + `KnowledgeGraph` +
`ProposalStore`. No new tables, no new storage. Each maps to a specific
operator intervention (review doctrine; create missing pages; attach
post-hoc provenance; trigger targeted re-extraction). See ADR-0012 for
the rationale + the Bases-vs-Python split.

### Companion: `dashboard.farming.base`

`src/memory_mission/memory/templates/dashboard.farming.base` — Obsidian
Bases YAML extending `dashboard.base` with five farming-native views
(domain coverage / decay flags / constitution review queue /
provenance debt / stub pages). Bases handles the always-on operator
UX; the Python primitives handle cross-cutting analytics that need
joins.

---

## Personal-plane temporal KG (`personal_brain/personal_kg.py`)

Per-employee instance of the firm `KnowledgeGraph`, scoped to a single
employee. ADR-0013 — extends ADR-0004 (MemPalace adoption) with the
state layer that ADR-0004 deferred.

### `PersonalKnowledgeGraph`

```python
class PersonalKnowledgeGraph:
    def __init__(self, *, db_path: Path | str, employee_id: str,
                 identity_resolver: IdentityResolver) -> None: ...

    @classmethod
    def for_employee(cls, *, firm_root, employee_id, identity_resolver
                    ) -> PersonalKnowledgeGraph: ...
```

Construct via `PersonalKnowledgeGraph.for_employee(...)` for the
standard path layout: `<firm_root>/personal/<validated_employee_id>/personal_kg.db`.

Properties:

- `employee_id: str` — validated via `validate_employee_id`
- `scope: str` — auto-derived as `f"employee_{employee_id}"`
- `identity_resolver: IdentityResolver` — the firm-wide resolver, shared with the firm KG (so personal and firm planes speak the same `p_<id>` / `o_<id>`)

### Auto-scope semantics

- **Writes** auto-apply `scope=self.scope` — `add_triple` doesn't
  even accept a `scope` parameter. Personal triples cannot escape
  their employee scope by construction.
- **Reads** auto-apply `viewer_scopes={self.scope}` — cross-employee
  triples are invisible even if they ever ended up in the same DB
  file (defense in depth; the per-employee path layout is the
  primary isolation).
- **`find_current_triple`** defends additionally: if the underlying
  KG returns a triple whose scope isn't this employee's scope,
  returns `None` (treats as no match).

### Surface (auto-scoped delegations to `KnowledgeGraph`)

| Method | Notes |
|---|---|
| `add_entity(name, *, entity_type, properties)` | Direct delegate; entities aren't scope-tagged in the schema |
| `add_triple(subject, predicate, obj, *, valid_from, valid_to, confidence, source_closet, source_file, tier)` | `scope` is NOT a parameter; auto-applied |
| `corroborate(subject, predicate, obj, *, confidence, source_closet, source_file)` | `scope` auto-applied; raises `ValueError` if existing triple's scope doesn't match (cross-leak guard) |
| `find_current_triple(subject, predicate, obj)` | Returns `None` for cross-scope hits |
| `invalidate(subject, predicate, obj, *, ended)` | Per-employee DB file means only this employee's triples ever match |
| `query_entity(name, *, as_of, direction)` | `viewer_scopes` auto-applied as `{employee_<id>}` |
| `query_relationship(predicate, *, as_of)` | Same |
| `timeline(entity_name)` | Same |

### Helper functions

- `employee_scope(employee_id) -> str` — returns the canonical scope string after validating the employee_id
- `open_personal_kg(*, firm_root, employee_id, identity_resolver)` — context manager that opens + closes the KG cleanly

### How it sits alongside MemPalace

```
PersonalMemoryBackend (Protocol, ADR-0004 + ADR-0013)
        │                              │
        ▼                              ▼
MemPalace (vector + citations)    PersonalKnowledgeGraph (temporal state)
firm/personal/<emp>/mempalace/    firm/personal/<emp>/personal_kg.db
```

Both serve the same employee. MemPalace handles "did I see something
about X?" recall; the personal KG handles "what do I currently
believe about X, when did it become true, and what evidence supports
it?" state.

`MemPalaceAdapter` exposes the KG via the new `personal_kg(employee_id)`
Protocol method, which lazily constructs + caches one
`PersonalKnowledgeGraph` per employee (analogous to its existing
`_PerEmployeeInstance` cache for the MemPalace palace).

---

## Skills registry (`skills/`)

Every skill is a directory with `SKILL.md` (frontmatter + body), registered in `skills/_index.md` (human-readable) and `skills/_manifest.jsonl` (machine-parsable, one line per skill).

### SKILL.md frontmatter

```yaml
---
name: <kebab-case-skill-name>
version: "YYYY-MM-DD"
triggers: ["natural phrases that should invoke this"]
tools: [<list of tool names this skill calls>]
preconditions:
  - "what must be true before running"
constraints:
  - "what the skill must not do"
category: <ingestion | governance | workflow | ...>
---
```

### Workflow skill invariant

> **Workflow skills never mutate firm truth directly.** They create
> Proposals + draft events. They do NOT call `promote()` directly,
> do NOT write to `KnowledgeGraph` directly, do NOT modify pages
> directly. The `review-proposals` workflow is the *single mutation
> surface* for firm truth.

This is a load-bearing architectural rule, not a stylistic preference.
The promotion gate is what makes the firm KG governable; any workflow
skill that bypasses it makes the entire governance story optional.
Every workflow-tier skill repeats this invariant in its constraints
block, so the rule is enforced at design time + visible at use time.

Shipped workflow skills that follow the invariant: `meeting-prep`
(reads-only — never writes; the strictest case), `update-deal-status`
(writes Update/Event proposals through `create_proposal`),
`record-ic-decision` (writes a tier=decision page + multiple typed
fact proposals through `create_proposal`).

The `extract-from-staging` skill is also write-through-proposal-only
(category: ingestion, but same rule). The administrator skills
(`detect-firm-candidates`, `onboard-venture-firm`) likewise create
proposals; they never bypass review.

### Shipped skills

17 shipped. All backfill skills route through the envelope path
(`make_<app>_connector` → `<app>_*_to_envelope` → `StagingWriter.write_envelope`).
All workflow skills follow the never-mutate-truth-directly invariant
(above).

| Skill | Category | Plane | What it does |
|---|---|---|---|
| `backfill-gmail` | ingestion | personal | Pull Gmail messages → personal staging |
| `backfill-outlook` | ingestion | personal | Pull Outlook (M365) messages → personal staging |
| `backfill-granola` | ingestion | personal | Pull Granola transcripts → personal staging |
| `backfill-calendar` | ingestion | personal | Pull Google Calendar events → personal staging |
| `backfill-firm-artefacts` | ingestion | firm | Admin: pull Drive docs → firm staging |
| `backfill-onedrive` | ingestion | firm | Admin: pull OneDrive + SharePoint document-library items → firm staging |
| `backfill-affinity` | ingestion | firm | Admin: pull Affinity orgs / persons / opportunities → firm staging |
| `backfill-attio` | ingestion | firm | Admin: pull Attio records (people / companies / deals / custom) → firm staging |
| `backfill-notion` | ingestion | firm | Admin: pull Notion pages + database rows → firm staging |
| `backfill-slack` | ingestion | mixed | Pull Slack messages; DMs/MPDMs → personal staging, channels → firm staging (helper enforces split) |
| `extract-from-staging` | ingestion | — | Host LLM extracts facts → fact staging |
| `review-proposals` | governance | firm | Surface pending proposals, human approves with rationale |
| `detect-firm-candidates` | governance | firm | Admin: scan personal planes, stage federated firm proposals |
| `meeting-prep` | workflow | — | Compile distilled `AgentContext` for a workflow agent |
| `update-deal-status` | workflow | firm | Venture overlay (P7-A): propose lifecycle/sub-state transitions through review |
| `record-ic-decision` | workflow | firm | Venture overlay (P7-A): propose tier=decision IC outcome with quorum + decision_rights validation |
| `onboard-venture-firm` | ingestion | firm | Venture overlay (P7-A): scaffold a new firm directory + propose constitution through review |

---

## Constants

| Constant | Value | Module | Meaning |
|---|---|---|---|
| `CORROBORATION_CAP` | `0.99` | `memory.knowledge_graph` | Max confidence via agent-path corroboration |
| `DEFAULT_TIER` | `"decision"` | `memory.tiers` | Default tier on every Triple and PageFrontmatter |
| `RRF_K` | `60` | `memory.search` | Reciprocal rank fusion constant |
| `COMPILED_TRUTH_BOOST` | `2.0` | `memory.search` | Score multiplier for compiled-truth zone matches |
| `VECTOR_RRF_BLEND` | `0.7` | `memory.search` | RRF weight in hybrid search final blend |
| `DEFAULT_MIN_EMPLOYEES` | `3` | `federated.detector` | Default federated detector threshold |
| `DEFAULT_MIN_SOURCES` | `3` | `federated.detector` | Same, for distinct source files |
| `MAX_RECENCY` | Various | `memory.salience` | Salience formula (lifted from agentic-stack) |

---

## Non-abstractions (things we deliberately don't model)

- **User/permissions roles.** No enum. Per-firm scopes are the authorization primitive, not hardcoded roles. (`ConnectorRole` exists in `ingestion/` but it's a *capability* taxonomy — which app fulfils which integration shape — not a user/permissions concept.)
- **Workflow state machines.** No generic workflow engine. Each skill is its own workflow in markdown.
- **LLM message formats.** Host agent owns them.
- **Vector embeddings as first-class models.** `EmbeddingProvider.embed(text) -> list[float]` and nothing else.
- **Network transport.** Every module is a Python library; host agent provides IPC.
