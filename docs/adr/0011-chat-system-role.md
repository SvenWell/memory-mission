---
type: ADR
id: "0011"
title: "chat_system role for Slack-shape integrations"
status: active
date: 2026-04-25
---

## Context

The P2 connector pack (ADR-0007) defines five capability roles:
``email``, ``calendar``, ``transcript``, ``document``, ``workspace``.
Each role expects a specific input shape: emails are point-in-time
messages, calendar events are bounded time-spans, transcripts are
self-contained recordings, documents are titled artefacts, workspace
records are typed CRM entities.

Slack does not fit any of these cleanly. A Slack channel is an
ongoing conversation thread. The atomic unit is a *message* with
parent-thread semantics. Visibility ranges from one-on-one DMs
(employee-private) through private channels (firm-internal) to
externally-shared channels (cross-firm). The `workspace` role
generalizes from CRM records — its consumers (extraction prompts,
sync-back, federated detection) assume structured fields, not
free-form message text.

Squeezing Slack into `workspace` would force every workspace-role
consumer to special-case Slack's shape. That's the wrong design
seam.

Microsoft Teams chat, Discord, and other team-comms substrates have
the same shape as Slack — message + thread + channel + DM/private/
external visibility. A new role serves them all.

## Decision

**Add a sixth `ConnectorRole.CHAT` value (`"chat"`) for Slack and
other message-stream substrates.** Bind via the same
`firm/systems.yaml` mechanism. Per-message envelope as the atomic
unit (mirrors Gmail). Plane split is encoded *inside* the envelope
helper based on Slack channel-type flags, not at the manifest
layer.

### Per-message envelope

One `NormalizedSourceItem` per Slack message. The envelope's
`external_id` is `f"{channel_id}_{message_ts}"` (Slack's canonical
message identity). `container_id` carries the `channel_id`. Thread
relationships surface in `visibility_metadata` as `slack_thread_ts`
when the message is a reply.

Considered alternatives:

- **Per-thread envelope** (root + all replies) — better semantic
  unit, but loses per-message author/timestamp signal that the
  proposal pipeline + federated detector need to attribute facts
  to individuals.
- **Per-channel-day envelope** — collapses busy channels into
  unreadable mega-blobs.

Per-message wins on attribution + uniformity with Gmail.

### Plane split: encoded in helper, not manifest

Slack mixes planes within a single binding: DMs/MPDMs are
employee-private (personal plane), regular channels are firm-shared
(firm plane), externally-shared channels are firm-plane with an
`external-shared` scope.

The manifest binding model (one `target_plane` per role) cannot
express this directly. Three options were considered:

1. **Helper-side plane override.** The helper inspects `slack_is_im`
   / `slack_is_mpim` and overrides `target_plane` to ``personal``;
   otherwise uses the manifest binding's `target_plane`. The
   manifest declares `target_plane: firm` (the dominant non-DM case).
   **Chosen.**
2. **Per-channel-type bindings.** Extend the manifest to allow
   `chat: { dm: {target_plane: personal, ...}, channel: {target_plane:
   firm, ...} }`. Architectural change to the manifest just for one
   role.
3. **Two roles.** `chat` (firm) and `chat_personal` (personal). The
   connector + envelope helper would have to be duplicated or
   parameterized awkwardly.

Option 1 keeps the manifest schema unchanged, localizes the
exception to the Slack helper, and matches the operator's mental
model (DMs are *always* personal regardless of any rule). The
exception is documented inline + here.

### Visibility metadata surface

The Slack envelope helper surfaces channel-type as top-level
metadata so manifest rules can map it cleanly:

- ``slack_channel_id`` (str)
- ``slack_channel_name`` (str)
- ``slack_is_im`` (bool — DM, single recipient)
- ``slack_is_mpim`` (bool — group DM)
- ``slack_is_private`` (bool — private channel)
- ``slack_is_shared`` (bool — shared with multiple workspaces)
- ``slack_is_ext_shared`` (bool — externally shared, includes
  non-firm members)
- ``slack_member_count`` (int when known)
- ``slack_thread_ts`` (str when message is a reply; None for root)
- ``labels`` (list[str], empty default)

Typical operator manifest:

```yaml
chat:
  app: slack
  target_plane: firm  # default for non-DM; helper overrides for is_im / is_mpim
  visibility_rules:
    - if_field: { slack_is_ext_shared: true }
      scope: external-shared
    - if_field: { slack_is_private: true }
      scope: partner-only
  default_visibility: firm-internal
```

DMs/MPDMs route to personal plane regardless; the manifest's
`target_scope` rules still apply (so an operator can scope DMs as
`employee-private` or whatever they want via a default).

## Options considered

- **Option A (chosen):** New `chat` role + per-message envelope +
  helper-side plane override.
- **Option B:** Reuse `workspace` role. Forces every workspace
  consumer to special-case Slack's message shape. Breaks the
  abstraction.
- **Option C:** New `chat` role + per-thread envelope. Loses author
  attribution for individual messages.
- **Option D:** Slack-specific bypass of the manifest model
  entirely. Loses the visibility-mapping uniformity that ADR-0007
  established.

## Rationale

1. **Attribution matters.** "Sven said X in #deals on April 22" is
   a discrete fact our federated detector needs to attribute.
   Per-message envelope preserves this; per-thread doesn't.

2. **The role taxonomy is meant to grow.** Microsoft Teams chat,
   Discord, IRC bridges, Mattermost, Zulip — all share the
   message-stream shape. A `chat` role serves them all without
   widening any existing role.

3. **DMs are not firm truth.** A reviewer should never see another
   employee's DMs in firm-plane staging. The plane-override
   guarantees this structurally — there is no manifest rule that
   can route a DM to the firm plane (the helper's override happens
   before scope mapping).

4. **One skill stays simpler.** A single `backfill-slack` skill
   that branches per channel-type internally is easier to operate
   than three separate skills (one per channel category).

## Consequences

- New required surface for any future chat substrate: declare a
  `chat` binding in `firm/systems.yaml`, write a per-app envelope
  helper that uses the plane-override pattern. ADR-0007's
  fail-closed visibility rules still apply.
- The manifest binding's `target_plane: firm` is the **default** for
  the `chat` role; per-binding `is_im` / `is_mpim` overrides it.
  This is the only role where target_plane can vary per item.
  Documented in the helper's docstring + the systems-yaml recipe.
- Reviewers trust that `staging/firm/slack/` contains zero DM
  content because the helper's override is structural, not
  config-driven.

## Follow-ups

- **Microsoft Teams chat helper.** When a pilot needs Teams chat
  ingestion, add `teams_message_to_envelope` against the same
  `chat` role + same Composio Microsoft Teams toolkit (the existing
  Teams toolkit covers messages but, per the connector research,
  does not expose meeting transcripts).
- **Promotion-pipeline awareness.** Approved facts extracted from
  DMs cannot be promoted to firm scope without an explicit reviewer
  override (the no-escalation rule from `permissions/policy.py`
  already handles this — a Sven-only DM extraction can only be
  proposed into scopes Sven himself has read access to).
- **External shared channels** are firm-plane but visible to
  non-firm members. Operators must remember that
  `external-shared` scope is appropriate; the manifest rule above
  enforces it.

## Related decisions

- **ADR-0002 (two planes, one-way bridge)** — Slack's plane split
  is consistent with the personal/firm boundary; the helper just
  picks the right one structurally.
- **ADR-0007 (capability-based connectors)** — adds `chat` as the
  sixth role; uses the same envelope + manifest substrate.
- **ADR-0008 (planned, P5 typed sync-back)** — write-side Slack
  actions (post message, react, archive channel) will route through
  the same sync-back path, gated on approved facts.
