# Venture firm permissions preset

Drop-in YAML + commentary for `firm/protocols/permissions.md`. Maps
common venture-firm roles (partner / principal / associate / IC member /
LP relations) to the existing `Policy` mechanics in
`src/memory_mission/permissions/policy.py` — scopes + per-employee
allowed scope sets + the no-escalation `can_propose` rule.

Aligned to the `decision_rights` declared in
`overlays/venture/constitution_seed.md` so the role hierarchy here
matches the authority hierarchy there.

## How to use

1. Copy this file into `<MM_WIKI_ROOT>/firm/protocols/permissions.md`.
2. Replace `<EMPLOYEE_ID>` placeholders with actual firm employees.
3. Tune the scope set per role to match the firm's actual confidentiality
   model (the defaults below are conservative — partner-only by default;
   firm-internal for shared portfolio data; external-shared only for
   genuinely public material).
4. The `Policy` loader (`parse_policy_markdown`) reads the YAML block
   below; the prose around it is for human reviewers.

## Scope vocabulary

The five scopes used by this preset, ordered roughly from least to most
restrictive:

| Scope | Meaning | Typical content |
|---|---|---|
| `public` | Visible to anyone; baseline | Public company info, generally-known facts |
| `external-shared` | Visible to external collaborators (co-investors, advisors) | Co-investor sync content, external newsletters |
| `firm-internal` | Visible to all firm employees | Portfolio status, weekly all-hands content |
| `partner-only` | Visible to partners + IC members | Active pipeline, partner sentiment, IC discussion |
| `lp-only` | Visible to partners + LP-relations team | LP correspondence, fund-level commitments |

`public` is implicit — every employee always sees public-scoped content
without it appearing in their explicit scope list (per `viewer_scopes`
in `permissions/policy.py`).

## Role presets

### Partner (full IC vote, all access)

Allowed scopes: `external-shared`, `firm-internal`, `partner-only`,
`lp-only`. Can propose into all five (the `can_propose` no-escalation
rule means a partner can propose into any scope they read).

### Principal (no LP access)

Allowed scopes: `external-shared`, `firm-internal`, `partner-only`.
Cannot propose into `lp-only`. May vote on IC if invited (fund-specific;
encoded in `firm/concepts/venture-constitution.md` `decision_rights`).

### Associate (firm-internal + external)

Allowed scopes: `external-shared`, `firm-internal`. Cannot read or
propose into `partner-only` or `lp-only`. Can extract facts from
sources they have access to (their own meeting notes, public
research, etc.).

### IC member (visiting / external IC)

Allowed scopes: `partner-only` (for IC content only). External IC
members see only the IC pipeline, not the full firm-internal feed.
Configurable per fund — some firms grant `firm-internal` too.

### LP relations (LP correspondence specialist)

Allowed scopes: `firm-internal`, `lp-only`. Does not need
`partner-only` access since they don't participate in pipeline
decisions. The narrow scope is deliberate — minimizes exposure of
deal flow to non-investment-decision personnel.

## YAML block (parsed by `parse_policy_markdown`)

```yaml
scopes:
  - name: public
    description: Visible to anyone; baseline
  - name: external-shared
    description: Visible to external collaborators
  - name: firm-internal
    description: Visible to all firm employees
  - name: partner-only
    description: Visible to partners + IC members
  - name: lp-only
    description: Visible to partners + LP-relations team

default_scope: firm-internal
constitutional_mode: false  # set to true to make coherence warnings BLOCK promotion

employees:
  # --- Partners ---
  - employee_id: <PARTNER_1_EMPLOYEE_ID>
    scopes: [external-shared, firm-internal, partner-only, lp-only]
  - employee_id: <PARTNER_2_EMPLOYEE_ID>
    scopes: [external-shared, firm-internal, partner-only, lp-only]
  - employee_id: <PARTNER_3_EMPLOYEE_ID>
    scopes: [external-shared, firm-internal, partner-only, lp-only]

  # --- Principal ---
  - employee_id: <PRINCIPAL_EMPLOYEE_ID>
    scopes: [external-shared, firm-internal, partner-only]

  # --- Associate ---
  - employee_id: <ASSOCIATE_EMPLOYEE_ID>
    scopes: [external-shared, firm-internal]

  # --- External IC member (if applicable) ---
  - employee_id: <EXTERNAL_IC_MEMBER_EMPLOYEE_ID>
    scopes: [partner-only]

  # --- LP relations ---
  - employee_id: <LP_RELATIONS_EMPLOYEE_ID>
    scopes: [firm-internal, lp-only]
```

## Constitutional mode

Set `constitutional_mode: true` only after the firm has
authored its `firm/concepts/venture-constitution.md` and is comfortable
with coherence warnings *blocking* promotion (vs surfacing as advisory).
The default is `false` — coherence warnings surface to the reviewer as
forcing questions but don't block.

For a firm just getting started, leave `constitutional_mode: false` and
let the reviewer decide each warning. Once the constitution is stable
(usually after 1–2 quarters of active use), flip to `true` to enforce
it structurally.

## No-escalation rule (already enforced)

`can_propose(policy, employee_id, target_scope)` rejects any proposal
into a scope the proposer doesn't read. Encoded in
`src/memory_mission/permissions/policy.py:149-160`. An associate
cannot propose into `partner-only`. An LP-relations specialist cannot
propose into `partner-only`. The promotion gate enforces this
structurally — no manifest tweak can disable it.

## Audit trail

Every `can_read` denial logs nothing (we don't log "Sven was denied
access to LP-only doc X" — that itself would leak metadata). Every
`promote` / `reject` / `reopen` decision logs to
`<observability_root>/<firm_id>/events.jsonl` with reviewer +
rationale + scope. The audit trail is structural, not optional.

## Override pattern

Firms with non-standard role structures (e.g., a single-partner solo
GP fund, or a multi-fund family-office shape) edit this file directly
in `<MM_WIKI_ROOT>/firm/protocols/permissions.md` after copying. The
overlay version here is a *starting point*, not a rigid template.

## Related

- `src/memory_mission/permissions/policy.py` — Policy / Scope /
  EmployeeEntry definitions; `can_read` + `can_propose` + `viewer_scopes`.
- `overlays/venture/constitution_seed.md` — `decision_rights` field
  encodes the same authority hierarchy at the deal-decision layer.
- `overlays/venture/firm_template.yaml` — connector visibility rules
  use the same scope vocabulary; an item ingested with
  `target_scope: partner-only` will only surface to employees with
  that scope in their allowed set.
