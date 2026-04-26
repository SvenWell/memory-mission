---
name: onboard-venture-firm
version: "2026-04-25"
triggers: ["onboard venture firm", "set up venture pilot", "scaffold venture overlay", "initialize venture firm", "bootstrap venture firm"]
tools: [knowledge_graph, brain_engine, drive_connector, durable_run, staging_writer, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved; running session is the firm administrator (typically a partner or operations lead)"
  - "MM_WIKI_ROOT is set + writable; the target firm directory either does not yet exist OR is empty"
  - "overlays/venture/ exists in the memory-mission install (shipped as part of P7-A)"
  - "drive connector has a ComposioClient injected if the firm wants the optional Drive backfill at onboarding time"
constraints:
  - "administrator-run only — this skill writes the firm's foundational config (constitution, permissions, systems.yaml)"
  - "do NOT overwrite existing firm config — if firm/systems.yaml or firm/protocols/permissions.md already exist, surface as a forcing question"
  - "the constitution copy MUST go through review-proposals as a tier=constitution page — onboarding does not auto-promote the constitution; the firm's partners review and approve"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "the optional Drive backfill writes to firm staging only — actual firm-truth promotion still requires review"
category: ingestion
---

# onboard-venture-firm — scaffold a new venture firm with the venture overlay

## What this does

Copies `overlays/venture/` artefacts into the target firm's directory,
optionally runs an initial `backfill-firm-artefacts` against the
firm's investment-thesis Drive folder, and surfaces the constitution
seed for partner review through the standard review-proposals gate.

After this skill runs, the firm has a working memory-mission
deployment with venture-flavored vocabulary, role presets, and
connector bindings — ready for the partners to review the constitution,
ratify the permissions, and begin running `update-deal-status` /
`record-ic-decision` workflows.

**Plane discipline:** Writes config files (firm-shared) +
optionally stages firm-source documents. Never touches personal
plane.

**Governance discipline:** The constitution and permissions presets
are *proposed*, not auto-applied. The firm's partners review the
constitution as a `tier=constitution` page through review-proposals
and either accept (it becomes firm doctrine) or amend.

## Workflow

1. **Verify preconditions.** Check that `MM_WIKI_ROOT` is set and
   writable. Check that `<MM_WIKI_ROOT>/firm/systems.yaml` and
   `<MM_WIKI_ROOT>/firm/protocols/permissions.md` do NOT exist (or
   surface as a forcing question if they do — overwrite is a
   conscious operator action). Check that `overlays/venture/`
   exists in the install path.

2. **Open an `observability_scope`** for the firm.

3. **Copy `firm_template.yaml` → `firm/systems.yaml`.** Bit-for-bit
   copy. The operator edits it after this skill runs to plug in
   actual list ids (Affinity), workspace ids (Notion), and
   employee-specific overrides. Surface a one-line note:
   "firm/systems.yaml copied from overlays/venture/firm_template.yaml.
   Edit the list ids + workspace ids per your actual setup."

4. **Copy `permissions_preset.md` → `firm/protocols/permissions.md`.**
   Bit-for-bit copy. The operator replaces the `<PARTNER_*_EMPLOYEE_ID>`
   placeholders with actual employee ids. Surface a one-line note:
   "firm/protocols/permissions.md copied from
   overlays/venture/permissions_preset.md. Replace placeholder
   employee ids with your actual partners + principals + associates."

5. **Stage `constitution_seed.md` for review.** Copy to
   `<MM_WIKI_ROOT>/staging/firm/onboarding/venture-constitution.md`,
   then create a Proposal with the constitution page as a
   `tier=constitution` candidate. The proposal goes into the
   review-proposals queue. The firm's partners read it, decide whether
   it matches their actual operating model, and either:
   - Approve (the constitution becomes `firm/concepts/venture-constitution.md`
     and is the authoritative operating doctrine going forward).
   - Amend + approve (edit the proposal, then promote).
   - Reject (the firm decides their operating model is too different
     for the venture overlay; they hand-author their own constitution).

6. **Copy page templates** to `<MM_WIKI_ROOT>/firm/_templates/venture/`.
   These are reference shapes the operator copies when creating their
   first deal / portfolio-company / IC-decision / DDQ-response page.
   Surface a one-line note pointing at the templates dir.

7. **(Optional) Initial Drive backfill.** If the operator supplies an
   investment-thesis Drive folder id, stand up the Drive connector
   and run a single `backfill-firm-artefacts` invocation against it.
   This pre-loads the firm's investment thesis docs into firm staging
   so the constitution review has thesis context to reference. The
   operator can decline this step if they don't have a thesis Drive
   folder yet.

8. **Write a setup-confirmation page** to
   `<MM_WIKI_ROOT>/firm/_setup/onboarding-2026-04-25.md` (datestamp on
   the actual run date) capturing:
   - Which overlay was applied (`venture`)
   - Which files were copied + where
   - Which placeholders need replacement (employee ids in
     permissions, list ids in systems.yaml)
   - Whether the optional Drive backfill ran
   - Pointer to the constitution proposal in the review queue
   - Next steps for the firm's partners (review constitution,
     populate placeholders, run first `update-deal-status`)

9. **Log a `DraftEvent`** for the onboarding run + the constitution
   proposal id. Surface the proposal id to the operator.

## What this skill does NOT do

- **Auto-promote the constitution.** The firm's partners review.
- **Auto-replace placeholders.** The operator manually edits
  permissions + systems.yaml after this skill runs. The skill writes
  the templates with placeholders intact.
- **Connect to external apps' OAuth flows.** Composio handles auth at
  the connector layer; the operator wires credentials before running
  the optional Drive backfill.
- **Run extraction.** Extraction is a separate skill
  (`extract-from-staging`) that runs after the firm's first staged
  documents are in place.
- **Migrate existing firm directories.** This skill is for new firms.
  Migration from an existing memory-mission deployment is a separate
  concern (migrate-firm-overlay skill, not yet shipped).

## On error

- `MM_WIKI_ROOT` unset / unwritable: error + ask operator to set.
- Existing `firm/systems.yaml` or `firm/protocols/permissions.md`:
  forcing question (overwrite confirms; cancel exits without changes).
- Drive backfill connector missing client: skip the optional backfill
  + warn (rest of skill proceeds).
- Constitution proposal create failure: surface the error + abort
  (the rest of the scaffold is in place; operator can manually
  create the proposal later via the standard pipeline).

## Self-rewrite hook

After every 5 uses (i.e. every 5 firm onboardings):

1. Check whether the same fields in `firm_template.yaml` get edited
   immediately by every firm; promote those to required-input in this
   skill (so the operator supplies them at run time rather than
   editing post-hoc).
2. Check whether the constitution gets amended in similar ways across
   firms; consider proposing those amendments to the canonical
   `overlays/venture/constitution_seed.md` for the next vertical
   release.
3. Commit: `skill-update: onboard-venture-firm, <one-line reason>`.

## Related

- `overlays/venture/` — the source of all artefacts this skill copies.
- `overlays/README.md` — the overlay contract.
- `skills/backfill-firm-artefacts/SKILL.md` — the Drive backfill this
  skill optionally invokes.
- `skills/review-proposals/SKILL.md` — handles the constitution review.
- `skills/update-deal-status/SKILL.md` + `skills/record-ic-decision/SKILL.md`
  — workflow skills the firm runs after onboarding.
