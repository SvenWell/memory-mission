"""Identity resolver Protocol + shared types.

Identifiers are ``type:value`` strings. Types in V1 are free-form
(``email``, ``linkedin``, ``twitter``, ``phone``, ``name``, ``domain``)
so adapters can add their own without a schema change. Local resolver
performs EXACT match only — fuzzy name matching is an opt-in future
addition (V1's conservative default).

Example:

    resolver = LocalIdentityResolver(db_path)
    alice = resolver.resolve(
        {"email:alice@acme.com", "name:Alice Smith"},
        entity_type="person",
        canonical_name="Alice Smith",
    )
    # alice == "p_a1b2c3" — stable forever

    # Later, from another source:
    alice_again = resolver.resolve(
        {"email:alice@acme.com", "linkedin:alice-smith-123"},
    )
    assert alice_again == alice  # same identity; linkedin bound as well
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

EntityKind = Literal["person", "organization"]


class Identity(BaseModel):
    """A resolved identity — stable across all sources that reference it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    entity_type: EntityKind
    canonical_name: str | None = None
    created_at: datetime


class IdentityConflictError(Exception):
    """Raised when one ``resolve()`` call's identifiers map to different
    existing identities.

    Example: the incoming set ``{"email:alice@acme.com", "email:alice@beta.com"}``
    where ``alice@acme.com`` is already bound to ``p_123`` and
    ``alice@beta.com`` is already bound to ``p_456``. The resolver does
    NOT auto-merge — that requires human review via
    ``KnowledgeGraph.merge_entities()`` (Step 14b).
    """

    def __init__(self, identifiers: set[str], matched_ids: set[str]) -> None:
        self.identifiers = frozenset(identifiers)
        self.matched_ids = frozenset(matched_ids)
        super().__init__(
            f"identifiers {sorted(identifiers)!r} map to multiple existing "
            f"identities {sorted(matched_ids)!r}; use "
            f"KnowledgeGraph.merge_entities() to resolve"
        )


@runtime_checkable
class IdentityResolver(Protocol):
    """Maps a set of identifiers to a stable entity ID.

    Implementations: ``LocalIdentityResolver`` (SQLite default),
    ``GraphOneResolver`` (external adapter, future), firm-custom
    adapters. All share this contract so host-agent code is resolver-
    agnostic.
    """

    def resolve(
        self,
        identifiers: set[str],
        *,
        entity_type: EntityKind = "person",
        canonical_name: str | None = None,
    ) -> str:
        """Return a stable ID for these identifiers.

        Behavior:

        - If any identifier already maps to an existing identity, return
          that identity's ID and bind any new identifiers to it.
        - If no identifier matches, create a new identity with all given
          identifiers bound.
        - If identifiers in the input map to MULTIPLE existing identities,
          raise ``IdentityConflictError`` — do not auto-merge.

        ``canonical_name`` is stored for display when creating a new
        identity; existing identities keep their original name unless the
        caller rebinds via a separate operation.
        """
        ...

    def lookup(self, identifier: str) -> str | None:
        """Return the identity ID currently bound to ``identifier``, or None."""
        ...

    def bindings(self, identity_id: str) -> list[str]:
        """Return all identifiers currently bound to this identity, sorted."""
        ...

    def get_identity(self, identity_id: str) -> Identity | None:
        """Return the identity record, or None if the ID does not exist."""
        ...


# ---------- Helpers ----------


def parse_identifier(identifier: str) -> tuple[str, str]:
    """Split a ``type:value`` identifier. Raises on missing or empty parts.

    Examples:

        parse_identifier("email:alice@acme.com")  # ("email", "alice@acme.com")
        parse_identifier("name:Alice Smith")       # ("name", "Alice Smith")

    Values may contain additional colons (e.g., URIs). Only the first ``:``
    separates type from value.
    """
    if ":" not in identifier:
        raise ValueError(f"identifier {identifier!r} must be in 'type:value' form")
    itype, _, value = identifier.partition(":")
    itype = itype.strip()
    value = value.strip()
    if not itype or not value:
        raise ValueError(f"identifier {identifier!r} has empty type or value")
    return itype, value


def make_entity_id(entity_type: EntityKind) -> str:
    """Generate a short, url-safe, prefix-tagged entity ID.

    ``person`` → ``p_<token>``, ``organization`` → ``o_<token>``. Random
    token is ~8 bytes of ``secrets`` entropy — collision-free at any
    realistic firm scale without a central coordinator.
    """
    prefix = "p" if entity_type == "person" else "o"
    return f"{prefix}_{secrets.token_urlsafe(8)}"
