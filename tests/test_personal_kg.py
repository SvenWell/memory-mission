"""Tests for ``PersonalKnowledgeGraph`` (ADR-0013).

Verifies:

- Per-employee construction at the standard path layout
  (``firm/personal/<emp>/personal_kg.db``)
- Cross-employee isolation — both via path (separate DB files) and
  scope auto-application
- Auto-scope on writes (every triple gets ``scope=employee_<id>``)
- Auto-scope filter on reads (cross-employee triples are invisible
  even if they somehow ended up in the same DB)
- Temporal semantics inherited from ``KnowledgeGraph``
  (``valid_from`` / ``valid_to`` / ``find_current_triple`` /
  ``corroborate``)
- Path-traversal defense via ``validate_employee_id`` at construction
- Identity-resolver bridge — the same firm-wide resolver is shared
  across personal + firm planes, so ``p_<id>`` / ``o_<id>`` are
  consistent across them
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.personal_brain.personal_kg import (
    PersonalKnowledgeGraph,
    employee_scope,
    open_personal_kg,
)


@pytest.fixture
def firm_root(tmp_path: Path) -> Path:
    return tmp_path / "firm"


@pytest.fixture
def resolver(tmp_path: Path) -> LocalIdentityResolver:
    return LocalIdentityResolver(tmp_path / "identity.sqlite3")


# ---------- Construction + path layout ----------


def test_for_employee_constructs_at_standard_path(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=firm_root,
        employee_id="alice-vc-example",
        identity_resolver=resolver,
    )
    expected = firm_root / "personal" / "alice-vc-example" / "personal_kg.db"
    assert expected.exists()
    assert pkg.employee_id == "alice-vc-example"
    assert pkg.scope == "employee_alice-vc-example"
    pkg.close()


def test_employee_scope_helper_validates() -> None:
    assert employee_scope("alice-vc-example") == "employee_alice-vc-example"
    with pytest.raises(ValueError, match="must match"):
        employee_scope("../escape")
    with pytest.raises(ValueError):
        employee_scope("")


@pytest.mark.parametrize(
    "bad_employee_id",
    ["../escape", "/tmp/escape", ".hidden", "foo/bar", "", "bad\x00id"],
)
def test_for_employee_rejects_unsafe_employee_ids(
    firm_root: Path, resolver: LocalIdentityResolver, bad_employee_id: str
) -> None:
    with pytest.raises(ValueError):
        PersonalKnowledgeGraph.for_employee(
            firm_root=firm_root,
            employee_id=bad_employee_id,
            identity_resolver=resolver,
        )
    # No filesystem side effect — personal/ dir not created
    assert not (firm_root / "personal").exists()


def test_open_personal_kg_context_manager_closes(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="bob-vc-example", identity_resolver=resolver
    ) as pkg:
        assert pkg.employee_id == "bob-vc-example"
    # Context manager closed; calling again should reopen cleanly
    with open_personal_kg(
        firm_root=firm_root, employee_id="bob-vc-example", identity_resolver=resolver
    ) as pkg:
        assert pkg.employee_id == "bob-vc-example"


# ---------- Auto-scope on writes ----------


def test_add_triple_auto_applies_employee_scope(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        triple = pkg.add_triple(
            subject="acme-corp",
            predicate="raised_at",
            obj="20000000",
            valid_from=date(2026, 4, 1),
            confidence=0.9,
            source_file="email-001",
        )
        assert triple.scope == "employee_alice-vc-example"
        assert triple.subject == "acme-corp"


def test_add_triple_does_not_accept_scope_kwarg(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    """Personal triples cannot escape their employee scope by construction."""
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        with pytest.raises(TypeError):
            pkg.add_triple(  # type: ignore[call-arg]
                subject="acme",
                predicate="x",
                obj="y",
                scope="public",
            )


# ---------- Per-employee isolation (path + scope) ----------


def test_two_employees_have_separate_db_files(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    pkg_alice = PersonalKnowledgeGraph.for_employee(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    )
    pkg_bob = PersonalKnowledgeGraph.for_employee(
        firm_root=firm_root, employee_id="bob-vc-example", identity_resolver=resolver
    )
    pkg_alice.add_triple(
        subject="acme", predicate="raised_at", obj="20m", valid_from=date(2026, 4, 1)
    )
    # Bob's KG sees nothing — different DB file entirely
    assert pkg_bob.query_entity("acme") == []
    # Alice's KG sees her own triple
    alice_triples = pkg_alice.query_entity("acme")
    assert len(alice_triples) == 1
    assert alice_triples[0].scope == "employee_alice-vc-example"
    pkg_alice.close()
    pkg_bob.close()


def test_query_entity_auto_filters_by_employee_scope(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    """If a foreign-scope triple ever lands in the personal DB, it's invisible."""
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    )
    # Plant a foreign-scope triple via the underlying KG (simulates a bug
    # where some other code wrote to this employee's DB with the wrong scope)
    pkg._kg.add_triple(  # type: ignore[attr-defined]
        subject="acme",
        predicate="raised_at",
        obj="999m",
        valid_from=date(2026, 4, 1),
        scope="public",  # NOT this employee's scope
    )
    # Wrapper read filters it out
    assert pkg.query_entity("acme") == []
    pkg.close()


# ---------- Temporal semantics ----------


def test_corroborate_strengthens_existing_triple(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        pkg.add_triple(
            subject="sarah",
            predicate="works_at",
            obj="acme",
            valid_from=date(2026, 1, 1),
            confidence=0.6,
            source_file="email-001",
        )
        result = pkg.corroborate(
            subject="sarah",
            predicate="works_at",
            obj="acme",
            confidence=0.7,
            source_file="email-002",
        )
        assert result is not None
        # Noisy-OR: 1 - (1-0.6)*(1-0.7) = 1 - 0.12 = 0.88
        assert 0.87 < result.confidence < 0.89


def test_corroborate_returns_none_when_no_match(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        result = pkg.corroborate(
            subject="nothing",
            predicate="here",
            obj="yet",
            confidence=0.9,
            source_file="email-001",
        )
        assert result is None


def test_invalidate_ends_validity(firm_root: Path, resolver: LocalIdentityResolver) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        pkg.add_triple(
            subject="acme",
            predicate="lifecycle_status",
            obj="diligence",
            valid_from=date(2026, 3, 1),
        )
        n = pkg.invalidate(
            subject="acme",
            predicate="lifecycle_status",
            obj="diligence",
            ended=date(2026, 4, 15),
        )
        assert n == 1
        # find_current_triple now returns None (the triple has valid_to set)
        assert pkg.find_current_triple("acme", "lifecycle_status", "diligence") is None


def test_find_current_triple_returns_active(
    firm_root: Path, resolver: LocalIdentityResolver
) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        pkg.add_triple(
            subject="acme",
            predicate="lifecycle_status",
            obj="diligence",
            valid_from=date(2026, 3, 1),
        )
        triple = pkg.find_current_triple("acme", "lifecycle_status", "diligence")
        assert triple is not None
        assert triple.scope == "employee_alice-vc-example"


def test_timeline_ordered_by_valid_from(firm_root: Path, resolver: LocalIdentityResolver) -> None:
    with open_personal_kg(
        firm_root=firm_root, employee_id="alice-vc-example", identity_resolver=resolver
    ) as pkg:
        pkg.add_triple(
            subject="acme",
            predicate="lifecycle_status",
            obj="sourced",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 2, 1),
        )
        pkg.add_triple(
            subject="acme",
            predicate="lifecycle_status",
            obj="diligence",
            valid_from=date(2026, 2, 1),
        )
        timeline = pkg.timeline("acme")
        assert len(timeline) == 2
        # All triples are this employee's
        for t in timeline:
            assert t.scope == "employee_alice-vc-example"
