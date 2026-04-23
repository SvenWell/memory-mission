"""Tests for the identity resolution layer (Step 14a).

Covers:
- ``parse_identifier`` / ``make_entity_id`` helpers
- ``LocalIdentityResolver.resolve`` happy path: first-seen creates,
  re-seen returns same ID
- ``resolve`` binds new identifiers to an existing identity
- ``IdentityConflictError`` raised when input spans multiple identities
- ``lookup`` / ``bindings`` / ``get_identity`` surface state correctly
- Per-firm isolation (separate DBs = separate identity namespaces)
- Persistence across instances
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.identity import (
    Identity,
    IdentityConflictError,
    IdentityResolver,
    LocalIdentityResolver,
    make_entity_id,
    parse_identifier,
)

# ---------- Helpers ----------


def test_parse_identifier_splits_type_and_value() -> None:
    assert parse_identifier("email:alice@acme.com") == ("email", "alice@acme.com")
    assert parse_identifier("linkedin:alice-smith") == ("linkedin", "alice-smith")


def test_parse_identifier_allows_value_with_colon() -> None:
    """URIs and URLs in values use colons; only the first splits."""
    assert parse_identifier("url:https://example.com/a") == (
        "url",
        "https://example.com/a",
    )


def test_parse_identifier_rejects_missing_colon() -> None:
    with pytest.raises(ValueError, match="type:value"):
        parse_identifier("alice@acme.com")


def test_parse_identifier_rejects_empty_parts() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_identifier(":alice@acme.com")
    with pytest.raises(ValueError, match="empty"):
        parse_identifier("email:")


def test_make_entity_id_prefixes_by_type() -> None:
    person_id = make_entity_id("person")
    org_id = make_entity_id("organization")
    assert person_id.startswith("p_")
    assert org_id.startswith("o_")
    # Two calls produce distinct IDs — randomness check
    assert make_entity_id("person") != make_entity_id("person")


# ---------- Fixtures ----------


@pytest.fixture
def resolver(tmp_path: Path) -> LocalIdentityResolver:
    return LocalIdentityResolver(tmp_path / "identity.sqlite3")


# ---------- Protocol surface ----------


def test_local_resolver_satisfies_protocol(resolver: LocalIdentityResolver) -> None:
    """Runtime-checkable Protocol membership — adapters land cleanly."""
    assert isinstance(resolver, IdentityResolver)


# ---------- Resolve happy paths ----------


def test_resolve_creates_new_identity_when_no_match(
    resolver: LocalIdentityResolver,
) -> None:
    alice_id = resolver.resolve(
        {"email:alice@acme.com", "name:Alice Smith"},
        entity_type="person",
        canonical_name="Alice Smith",
    )
    assert alice_id.startswith("p_")
    identity = resolver.get_identity(alice_id)
    assert identity == Identity(
        id=alice_id,
        entity_type="person",
        canonical_name="Alice Smith",
        created_at=identity.created_at,  # timestamp populated by DB
    )


def test_resolve_returns_existing_id_on_match(
    resolver: LocalIdentityResolver,
) -> None:
    first = resolver.resolve({"email:alice@acme.com"}, canonical_name="Alice")
    second = resolver.resolve({"email:alice@acme.com", "linkedin:alice-s"})
    assert first == second


def test_resolve_binds_new_identifiers_to_existing_identity(
    resolver: LocalIdentityResolver,
) -> None:
    alice = resolver.resolve({"email:alice@acme.com"}, canonical_name="Alice")
    resolver.resolve({"email:alice@acme.com", "linkedin:alice-s", "twitter:@alice"})
    assert set(resolver.bindings(alice)) == {
        "email:alice@acme.com",
        "linkedin:alice-s",
        "twitter:@alice",
    }


def test_resolve_is_idempotent_on_identical_input(
    resolver: LocalIdentityResolver,
) -> None:
    idset = {"email:alice@acme.com", "linkedin:alice-s"}
    a = resolver.resolve(idset)
    b = resolver.resolve(idset)
    assert a == b
    # No duplicate bindings
    assert sorted(resolver.bindings(a)) == sorted(idset)


def test_resolve_creates_distinct_identities_for_distinct_identifiers(
    resolver: LocalIdentityResolver,
) -> None:
    alice = resolver.resolve({"email:alice@acme.com"})
    bob = resolver.resolve({"email:bob@acme.com"})
    assert alice != bob


def test_resolve_rejects_empty_set(resolver: LocalIdentityResolver) -> None:
    with pytest.raises(ValueError, match="at least one"):
        resolver.resolve(set())


def test_resolve_rejects_malformed_identifier(
    resolver: LocalIdentityResolver,
) -> None:
    with pytest.raises(ValueError, match="type:value"):
        resolver.resolve({"alice@acme.com"})


# ---------- Conflict ----------


def test_resolve_raises_conflict_on_multi_identity_input(
    resolver: LocalIdentityResolver,
) -> None:
    """Two previously-separate identities in one input → IdentityConflictError."""
    alice = resolver.resolve({"email:alice@acme.com"})
    duplicate_alice = resolver.resolve({"email:alice@beta.com"})
    assert alice != duplicate_alice

    with pytest.raises(IdentityConflictError) as exc_info:
        resolver.resolve({"email:alice@acme.com", "email:alice@beta.com"})
    conflict = exc_info.value
    assert conflict.matched_ids == frozenset({alice, duplicate_alice})
    assert "merge_entities" in str(conflict)


def test_resolve_conflict_leaves_db_unchanged(
    resolver: LocalIdentityResolver,
) -> None:
    """Conflict path must not create a third identity or add bindings."""
    a = resolver.resolve({"email:alice@acme.com"})
    b = resolver.resolve({"email:alice@beta.com"})
    before_a_bindings = resolver.bindings(a)
    before_b_bindings = resolver.bindings(b)

    with pytest.raises(IdentityConflictError):
        resolver.resolve({"email:alice@acme.com", "email:alice@beta.com", "linkedin:alice-s"})

    assert resolver.bindings(a) == before_a_bindings
    assert resolver.bindings(b) == before_b_bindings
    # linkedin:alice-s should NOT have been bound to either
    assert resolver.lookup("linkedin:alice-s") is None


# ---------- Lookup / state surface ----------


def test_lookup_returns_identity_id_for_bound_identifier(
    resolver: LocalIdentityResolver,
) -> None:
    alice = resolver.resolve({"email:alice@acme.com"})
    assert resolver.lookup("email:alice@acme.com") == alice


def test_lookup_returns_none_for_unknown_identifier(
    resolver: LocalIdentityResolver,
) -> None:
    assert resolver.lookup("email:nobody@nowhere.com") is None


def test_lookup_rejects_malformed_identifier(
    resolver: LocalIdentityResolver,
) -> None:
    with pytest.raises(ValueError, match="type:value"):
        resolver.lookup("not-an-identifier")


def test_bindings_sorted_alphabetically(resolver: LocalIdentityResolver) -> None:
    alice = resolver.resolve({"linkedin:alice-s", "email:alice@acme.com", "twitter:@alice"})
    assert resolver.bindings(alice) == [
        "email:alice@acme.com",
        "linkedin:alice-s",
        "twitter:@alice",
    ]


def test_bindings_returns_empty_for_unknown_identity(
    resolver: LocalIdentityResolver,
) -> None:
    assert resolver.bindings("p_nonexistent") == []


def test_get_identity_returns_none_for_unknown_id(
    resolver: LocalIdentityResolver,
) -> None:
    assert resolver.get_identity("p_nonexistent") is None


def test_get_identity_returns_canonical_name_and_type(
    resolver: LocalIdentityResolver,
) -> None:
    acme = resolver.resolve(
        {"domain:acme.com"},
        entity_type="organization",
        canonical_name="Acme Corporation",
    )
    identity = resolver.get_identity(acme)
    assert identity is not None
    assert identity.entity_type == "organization"
    assert identity.canonical_name == "Acme Corporation"


def test_organization_ids_use_o_prefix(resolver: LocalIdentityResolver) -> None:
    acme = resolver.resolve({"domain:acme.com"}, entity_type="organization", canonical_name="Acme")
    assert acme.startswith("o_")


# ---------- Persistence / isolation ----------


def test_resolver_persists_across_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.sqlite3"
    with LocalIdentityResolver(db_path) as r1:
        alice = r1.resolve({"email:alice@acme.com"}, canonical_name="Alice")

    with LocalIdentityResolver(db_path) as r2:
        assert r2.lookup("email:alice@acme.com") == alice
        identity = r2.get_identity(alice)
        assert identity is not None
        assert identity.canonical_name == "Alice"


def test_per_firm_isolation(tmp_path: Path) -> None:
    """Different DB paths = different identity namespaces."""
    with (
        LocalIdentityResolver(tmp_path / "firm-a.sqlite3") as a,
        LocalIdentityResolver(tmp_path / "firm-b.sqlite3") as b,
    ):
        alice_a = a.resolve({"email:alice@acme.com"})
        alice_b = b.resolve({"email:alice@acme.com"})
        # Same identifier, two different firms → distinct IDs.
        assert alice_a != alice_b
        # Firm A cannot see firm B's bindings and vice versa.
        assert len(a.bindings(alice_b)) == 0
        assert len(b.bindings(alice_a)) == 0


def test_close_is_idempotent(tmp_path: Path) -> None:
    r = LocalIdentityResolver(tmp_path / "x.sqlite3")
    r.close()
    r.close()  # must not raise


def test_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "identity.sqlite3"
    LocalIdentityResolver(nested).close()
    assert nested.exists()
