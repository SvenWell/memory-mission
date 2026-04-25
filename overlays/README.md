# overlays/

Vertical-specific operating models that ride on top of memory-mission's
generic governance core. Per the architecture rule that "vertical-specific
taxonomies live in a config layer, not core schema," each overlay is a
self-contained directory under `overlays/<vertical>/` that an
administrator copies into a target firm during onboarding.

**Core stays domain-agnostic.** Predicates, page templates, lifecycle
vocabulary, role presets, and workflow skills that are venture-specific
(or PE-specific, or wealth-specific) live here — not in `src/`.

## Overlay contract

Every overlay directory ships these files:

| File | Purpose | Consumed by |
|---|---|---|
| `firm_template.yaml` | Default `firm/systems.yaml` for the vertical — pre-wired connector bindings + visibility rules tuned to the vertical's typical CRM / document / chat stack | `load_systems_manifest` (`src/memory_mission/ingestion/systems_manifest.py`) |
| `constitution_seed.md` | A single `tier: constitution` page that seeds the vertical's operating doctrine — lifecycle stages, decision rights, authoritative vocabulary. Frontmatter uses `extra` fields (e.g. `lifecycle_stages`, `ic_quorum`) to encode vertical-specific structure that the core PageFrontmatter passes through unchanged | `parse_page` / `render_page` (`src/memory_mission/memory/pages.py`) — `extra="allow"` keeps the fields round-tripping |
| `prompt_examples.md` | Extraction-prompt addenda teaching the host LLM the vertical's predicate vocabulary (e.g. for venture: `lifecycle_status`, `ddq_status`, `ic_decision`) and giving it 1–3 worked examples in the vertical's language. Pure prompt-tuning artifact — no code change | Host agent merges into the extraction call alongside `EXTRACTION_PROMPT` (`src/memory_mission/extraction/prompts.py`) |
| `permissions_preset.md` | Role presets that map to the existing `Policy` tier-floor + scope mechanics. Drop-in YAML + commentary for `firm/protocols/permissions.md` | `Policy` (`src/memory_mission/permissions/policy.py`) |
| `page_templates/` | Page skeletons per business object (e.g. for venture: `deal.md`, `portfolio_company.md`, `ic_decision.md`, `ddq_response.md`). Frontmatter uses existing core domains (`domain: deals` etc.) with `extra` fields for type-specific keys | Operators copy a template into `firm/<domain>/<slug>.md` to bootstrap a new business-object record |
| `workflows/` | (Optional) Vertical-flavored workflow skill markdowns. May reference `compile_agent_context` with vertical-specific `role` values | Host agent skill loader |

## Why overlays, not core

1. **Vertical taxonomy churn doesn't propagate.** Lifecycle stages
   change. IC quorum sizes change. New deal types emerge. None of that
   should require a core release.
2. **Multi-vertical from day one.** The same core serves
   `overlays/venture/`, `overlays/pe/`, and `overlays/wealth/` without a
   fork — they're just different operator config bundles.
3. **Onboarding compresses.** A new firm runs
   `skills/onboard-venture-firm` (or equivalent), which copies the
   overlay into their firm directory and runs the initial backfill
   against their connected accounts. First useful day is hours, not
   weeks.
4. **Customer-editable.** Firms own their overlay copy. They can
   override `lifecycle_stages` for their own operating model without
   forking memory-mission.

## Shipped overlays

| Overlay | Status | Notes |
|---|---|---|
| `venture/` | active | First vertical operating layer. Designed for VC firms (sourcing → diligence → IC → portfolio lifecycle). |
| `pe/` | planned | PE-specific: longer hold periods, operational improvement workflows, exit modeling. |
| `wealth/` | planned | Wealth management: client households, model portfolios, regulatory disclosures. |

## Not in scope for an overlay

- **Core schema changes.** New predicates and frontmatter fields are
  encoded as data (`prompt_examples.md` for predicate vocab, `extra=allow`
  for frontmatter). They never require modifying
  `src/memory_mission/`.
- **New connector apps.** Connectors are in `src/memory_mission/ingestion/connectors/`
  and are vertical-agnostic. An overlay binds existing connectors via
  `firm_template.yaml`; it does not introduce new ones.
- **New workflow primitives.** Overlays compose `compile_agent_context` +
  `create_proposal` + `review-proposals` — they don't add new core
  workflow infrastructure.

If an overlay needs something the core doesn't provide, the right
move is to first ask whether a *second* vertical would also need it.
If yes, it's a core feature. If no, it's overlay config.
