# Recipe: Firm-plane dashboard in Obsidian

A native Obsidian Bases view that surfaces what needs attention in the firm's governed memory. Non-technical partners open the vault and immediately see low-confidence facts, stale pages, recent changes, and authoritative content grouped by tier.

**Requires:** Obsidian v1.9.10 or later. Bases is a core feature since August 2025 — no plugin to install.

## Install

1. Copy the template into your firm root:

   ```bash
   cp src/memory_mission/memory/templates/dashboard.base \
      <vault_root>/firm/dashboard.base
   ```

2. Open the vault in Obsidian.
3. Open `firm/dashboard.base` — Bases auto-discovers `.base` files.

That's it. No configuration; the views are data-driven off your existing page frontmatter.

## What you get

Five views, all scoped to firm-plane pages (personal plane is intentionally hidden — personal context belongs to the owning employee's agent, not the shared dashboard):

| View | Filter | Purpose |
|---|---|---|
| **Recent changes** | `age_days ≤ 7`, grouped by tier | "What moved this week?" |
| **Low confidence** | `confidence < 0.7`, grouped by domain | "What needs corroboration?" |
| **Stale or unreviewed** | `age_days > 90` OR `reviewed_at IS NULL` | "What's been ignored?" |
| **Constitution + doctrine** | `tier ∈ {constitution, doctrine}` | "The authoritative set at a glance" |
| **By domain** | All firm pages, grouped by domain | Browse by MECE category |

## Fields the dashboard reads

Your pages already carry most of these. The only new one is `reviewed_at`, added in the Move 2 polish pass.

| Frontmatter field | Source | Used by |
|---|---|---|
| `confidence` | Shipped since Step 6a; Bayesian corroboration updates it | Low confidence view |
| `tier` | Shipped in Step 15a; default `decision` | Recent changes + Constitution views |
| `domain` | Shipped since Step 6a; MECE taxonomy | By-domain grouping |
| `reviewed_at` | New (Move 2); set explicitly when a reviewer signs off | Stale view |
| `file.mtime` | Filesystem | Recent + stale computations |

### Setting `reviewed_at`

No automation ships. When a reviewer finishes curating a page, they (or a reviewer skill) set `reviewed_at` in frontmatter:

```yaml
---
slug: acme-corp
title: Acme Corporation
domain: companies
tier: policy
confidence: 0.95
reviewed_at: 2026-04-22T14:30:00+00:00
---
```

This is a deliberate editorial act; automatic back-stamping defeats the purpose. The `review-proposals` skill is a natural place to wire it — after approving a proposal, update the target page's `reviewed_at`.

## Customizing the views

The `.base` file is plain YAML. Edit it like any Obsidian file. Common tweaks:

- **Add a tier filter to Recent changes:** `tier != "decision"` to surface only authoritative movement.
- **Change the staleness threshold:** edit the `age_days > 90` number.
- **Add a view for coherence warnings:** not currently supported because coherence events live in the observability log, not in page frontmatter. The contradiction callout (Move 3) surfaces them at the page level instead. A future recipe can add a small scan script that writes a manifest page Bases can query.

## Why Bases and not Dataview

Bases is native Obsidian (no plugin), survives future Obsidian updates by design, and the YAML syntax is easier to review in git than the Dataview query language. The claude-obsidian project ships both (legacy Dataview dashboard alongside the Bases primary). We ship only Bases — simpler, fewer dependencies, and the feature set we need.
