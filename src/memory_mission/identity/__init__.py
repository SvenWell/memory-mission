"""Identity resolution — stable IDs for people and organizations.

Why this exists: the LLM emits entity names like "alice-smith",
"a-smith", and "alice-at-acme" from different source documents,
producing three different KG nodes for the same person. Without
identity resolution, relationship history fragments, meeting-prep
can't aggregate what the firm knows about a person, and the
federated detector produces false negatives across employee planes.

Shape (Step 14a):

- ``IdentityResolver`` Protocol — ``resolve(identifiers)`` returns a
  stable ID. The resolver decides whether the identifiers belong to
  an existing identity or need a new one. Adapters (Graph One, Clay,
  firm-custom) implement the same Protocol.
- ``LocalIdentityResolver`` — SQLite-backed default. Exact match on
  typed identifiers (``email:alice@acme.com``, ``linkedin:alice-s``,
  etc.). Conservative by design: two different-looking names without
  a shared typed identifier stay as two identities. Avoids false
  positives (merging unrelated people) at the cost of occasional
  false negatives (treating one person as two until a shared
  identifier arrives).
- ``PersonID`` / ``OrgID`` — ``str`` aliases prefixed ``p_`` / ``o_``.
  Stable across the firm; never reused.
- ``IdentityConflictError`` — raised when one resolve() call's identifiers
  map to different existing identities (ambiguity). Caller decides
  whether to merge (see ``KnowledgeGraph.merge_entities`` in 14b) or
  reject.

Adapter pattern matches Composio connectors in ``connectors/``: ship
the Protocol + a local impl, host agents wire external services by
satisfying the same Protocol.
"""

from memory_mission.identity.base import (
    Identity,
    IdentityConflictError,
    IdentityResolver,
    make_entity_id,
    parse_identifier,
)
from memory_mission.identity.local import LocalIdentityResolver

__all__ = [
    "Identity",
    "IdentityConflictError",
    "IdentityResolver",
    "LocalIdentityResolver",
    "make_entity_id",
    "parse_identifier",
]
