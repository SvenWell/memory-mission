"""Protocol-level contract tests for ``PersonalMemoryBackend``.

Parameterized over backend implementations so the same suite runs
against:

- ``_FakeInMemoryBackend`` (defined in this file) — exercises the
  Protocol contract, gives us baseline assertions today
- ``MemPalaceAdapter`` — the adopted personal substrate
- An eventual replacement impl — same contract, same tests

The contract assertions are substrate-agnostic: every impl must enforce
employee isolation, return citations on hits, route candidate facts
through the bridge shape, etc.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from memory_mission.extraction.schema import ExtractedFact
from memory_mission.identity.base import IdentityResolver
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.ingestion.roles import NormalizedSourceItem
from memory_mission.personal_brain.backend import (
    CandidateFact,
    Citation,
    EntityRef,
    IngestResult,
    PersonalHit,
    PersonalMemoryBackend,
    WorkingContext,
)
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from tests.fixtures.pilot_tasks.scenarios import (
    ALL_SCENARIOS,
    Scenario,
)

# ---------- Reference fake impl ----------


class _FakeInMemoryBackend:
    """Reference impl for the contract. Per-employee dict-of-items.

    Not production. Exists so the contract test has a baseline that
    passes today and a stable harness MemPalaceAdapter slots into.
    """

    def __init__(self, tmp_root: Path | None = None) -> None:
        self._items: dict[str, list[NormalizedSourceItem]] = {}
        self._hit_lookup: dict[tuple[str, str], NormalizedSourceItem] = {}
        # Personal KG support (ADR-0013): per-employee KGs lazily
        # constructed under a tmp root if the test wants to exercise
        # the personal_kg() Protocol method.
        self._personal_kgs: dict[str, PersonalKnowledgeGraph] = {}
        self._tmp_root: Path = tmp_root or Path(tempfile.mkdtemp(prefix="fake-pkg-"))
        self._identity_resolver: IdentityResolver | None = None

    def _resolver(self) -> IdentityResolver:
        if self._identity_resolver is None:
            self._identity_resolver = LocalIdentityResolver(self._tmp_root / "identity.sqlite")
        return self._identity_resolver

    def personal_kg(self, employee_id: str) -> PersonalKnowledgeGraph:
        if employee_id not in self._personal_kgs:
            self._personal_kgs[employee_id] = PersonalKnowledgeGraph.for_employee(
                firm_root=self._tmp_root,
                employee_id=employee_id,
                identity_resolver=self._resolver(),
            )
        return self._personal_kgs[employee_id]

    def ingest(
        self,
        item: NormalizedSourceItem,
        *,
        employee_id: str,
    ) -> IngestResult:
        bucket = self._items.setdefault(employee_id, [])
        bucket.append(item)
        self._hit_lookup[(employee_id, item.external_id)] = item
        return IngestResult(items_ingested=1)

    def query(
        self,
        question: str,
        *,
        employee_id: str,
        limit: int = 10,
    ) -> list[PersonalHit]:
        bucket = self._items.get(employee_id, [])
        q = question.lower()
        hits: list[PersonalHit] = []
        for item in bucket:
            haystack = f"{item.title} {item.body}".lower()
            if any(token in haystack for token in q.split()):
                hits.append(self._to_hit(item))
        hits.sort(key=lambda h: h.cited_at, reverse=True)
        return hits[:limit]

    def citations(
        self,
        hit_id: str,
        *,
        employee_id: str,
    ) -> list[Citation]:
        item = self._hit_lookup.get((employee_id, hit_id))
        return [self._to_citation(item)] if item else []

    def resolve_entity(
        self,
        identifiers: list[str],
        *,
        employee_id: str,
    ) -> EntityRef:
        return EntityRef(
            entity_id=f"p_{employee_id}_{abs(hash(tuple(identifiers))) % 100000:05d}",
            identifiers=identifiers,
        )

    def working_context(
        self,
        *,
        employee_id: str,
        task: str,
    ) -> WorkingContext:
        relevant = self.query(task, employee_id=employee_id, limit=5)
        return WorkingContext(
            employee_id=employee_id,
            task=task,
            relevant_hits=relevant,
        )

    def candidate_facts(
        self,
        *,
        employee_id: str,
        since: datetime | None = None,
    ) -> Iterable[CandidateFact]:
        bucket = self._items.get(employee_id, [])
        for item in bucket:
            if since is not None and item.modified_at < since:
                continue
            if item.source_role.value not in ("transcript", "email"):
                continue
            yield CandidateFact(
                employee_id=employee_id,
                fact_kind="event",
                payload={
                    "kind": "event",
                    "confidence": 0.5,
                    "support_quote": item.body[:150],
                    "entity_name": item.title,
                    "description": item.title,
                },
                citations=[self._to_citation(item)],
                confidence=0.5,
                surfaced_at=datetime.now(UTC),
            )

    def _to_hit(self, item: NormalizedSourceItem) -> PersonalHit:
        return PersonalHit(
            hit_id=item.external_id,
            title=item.title,
            snippet=item.body[:200],
            score=0.5,
            cited_at=item.modified_at,
            citations=[self._to_citation(item)],
        )

    def _to_citation(self, item: NormalizedSourceItem) -> Citation:
        return Citation(
            source_role=item.source_role.value,
            concrete_app=item.concrete_app,
            external_id=item.external_id,
            container_id=item.container_id,
            url=item.url,
            modified_at=item.modified_at,
            excerpt=item.body[:200],
        )


@pytest.fixture
def fake_backend() -> PersonalMemoryBackend:
    return _FakeInMemoryBackend()


@pytest.fixture
def mempalace_backend(tmp_path) -> PersonalMemoryBackend:  # type: ignore[no-untyped-def]
    """MemPalaceAdapter wired to a tmp firm root + LocalIdentityResolver."""
    from memory_mission.identity.local import LocalIdentityResolver
    from memory_mission.personal_brain.mempalace_adapter import MemPalaceAdapter

    identity = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    return MemPalaceAdapter(firm_root=tmp_path, identity_resolver=identity)


# Every contract test parametrizes over both backends. If MemPalaceAdapter
# fails any of these, ADR-0004's acceptance gate falls.
@pytest.fixture(params=["fake", "mempalace"])
def backend(request: pytest.FixtureRequest) -> PersonalMemoryBackend:  # type: ignore[no-untyped-def]
    return request.getfixturevalue(f"{request.param}_backend")


# ---------- Protocol-shape tests ----------


def test_fake_satisfies_protocol() -> None:
    backend = _FakeInMemoryBackend()
    assert isinstance(backend, PersonalMemoryBackend)


def test_ingest_returns_ingest_result(backend: PersonalMemoryBackend) -> None:
    scenario = ALL_SCENARIOS[0]()
    item = scenario.corpus[0]
    result = backend.ingest(item, employee_id=scenario.employee_id)
    assert isinstance(result, IngestResult)
    assert result.items_ingested == 1


def test_query_returns_typed_hits(backend: PersonalMemoryBackend) -> None:
    scenario = ALL_SCENARIOS[0]()
    for item in scenario.corpus:
        backend.ingest(item, employee_id=scenario.employee_id)
    hits = backend.query(scenario.query, employee_id=scenario.employee_id)
    assert all(isinstance(h, PersonalHit) for h in hits)


def test_query_hit_citation_external_id_matches_hit_id(backend: PersonalMemoryBackend) -> None:
    scenario = ALL_SCENARIOS[0]()
    for item in scenario.corpus:
        backend.ingest(item, employee_id=scenario.employee_id)

    hits = backend.query(scenario.query, employee_id=scenario.employee_id)

    assert hits
    for hit in hits:
        assert hit.citations
        assert all(c.external_id == hit.hit_id for c in hit.citations)


# ---------- Acceptance scenarios ----------


@pytest.mark.parametrize("scenario_factory", ALL_SCENARIOS, ids=lambda s: s.__name__)
def test_acceptance_scenario(
    backend: PersonalMemoryBackend,
    scenario_factory: Any,
) -> None:
    scenario: Scenario = scenario_factory()
    for item in scenario.corpus:
        backend.ingest(item, employee_id=scenario.employee_id)

    hits = backend.query(scenario.query, employee_id=scenario.employee_id)
    assert len(hits) >= scenario.expected_min_hits, (
        f"{scenario.name}: expected ≥{scenario.expected_min_hits} hits, got {len(hits)}"
    )
    assert all(len(h.citations) >= scenario.expected_citation_count_min for h in hits), (
        f"{scenario.name}: every hit must carry ≥{scenario.expected_citation_count_min} citation"
    )
    if scenario.expected_entity_in_results:
        haystack = " ".join(f"{h.title} {h.snippet}".lower() for h in hits)
        assert scenario.expected_entity_in_results.lower() in haystack, (
            f"{scenario.name}: expected '{scenario.expected_entity_in_results}' in hit content"
        )


# ---------- Employee-private isolation ----------


def test_employee_isolation_under_concurrent_writes(
    backend: PersonalMemoryBackend,
) -> None:
    """A query under one employee_id must NEVER return data ingested under another."""
    alice_scenario = ALL_SCENARIOS[0]()
    bob_scenario = ALL_SCENARIOS[2]()
    assert alice_scenario.employee_id != bob_scenario.employee_id

    for item in alice_scenario.corpus:
        backend.ingest(item, employee_id=alice_scenario.employee_id)
    for item in bob_scenario.corpus:
        backend.ingest(item, employee_id=bob_scenario.employee_id)

    alice_hits = backend.query(alice_scenario.query, employee_id=alice_scenario.employee_id)
    bob_hits = backend.query(bob_scenario.query, employee_id=bob_scenario.employee_id)

    alice_ext_ids = {item.external_id for item in alice_scenario.corpus}
    bob_ext_ids = {item.external_id for item in bob_scenario.corpus}

    for hit in alice_hits:
        assert hit.hit_id not in bob_ext_ids, (
            f"Bob's data leaked into Alice's query: hit_id={hit.hit_id}"
        )
    for hit in bob_hits:
        assert hit.hit_id not in alice_ext_ids, (
            f"Alice's data leaked into Bob's query: hit_id={hit.hit_id}"
        )


def test_citations_employee_scoped(backend: PersonalMemoryBackend) -> None:
    """citations() under one employee MUST return empty for another employee's hit_id."""
    alice = "alice-vc-example"
    bob = "bob-vc-example"
    scenario = ALL_SCENARIOS[0]()
    item = scenario.corpus[0]
    backend.ingest(item, employee_id=alice)

    alice_citations = backend.citations(item.external_id, employee_id=alice)
    bob_citations = backend.citations(item.external_id, employee_id=bob)

    assert len(alice_citations) >= 1
    assert bob_citations == [], (
        f"Bob retrieved citations for Alice's hit ({len(bob_citations)} cited)"
    )


# ---------- Bridge into firm proposals ----------


def test_candidate_facts_payload_matches_extracted_fact_shape(
    backend: PersonalMemoryBackend,
) -> None:
    """CandidateFact.payload must conform to the ExtractedFact discriminator shape.

    The proposal pipeline consumes candidates without conversion. This
    test asserts the payload always carries a ``kind`` discriminator.
    """
    scenario = ALL_SCENARIOS[1]()  # followup_commitments
    for item in scenario.corpus:
        backend.ingest(item, employee_id=scenario.employee_id)

    facts = list(backend.candidate_facts(employee_id=scenario.employee_id))
    assert len(facts) >= 1
    fact_adapter = TypeAdapter(ExtractedFact)
    for fact in facts:
        parsed = fact_adapter.validate_python(fact.payload)
        assert fact.fact_kind == fact.payload["kind"]
        assert fact.fact_kind == parsed.kind


def test_candidate_facts_employee_scoped(backend: PersonalMemoryBackend) -> None:
    """candidate_facts() under one employee never surfaces another's items."""
    alice = "alice-vc-example"
    bob = "bob-vc-example"
    item = ALL_SCENARIOS[0]().corpus[0]
    backend.ingest(item, employee_id=alice)

    alice_facts = list(backend.candidate_facts(employee_id=alice))
    bob_facts = list(backend.candidate_facts(employee_id=bob))

    assert any(f.employee_id == alice for f in alice_facts)
    assert all(f.employee_id != alice for f in bob_facts)


# ---------- Working context ----------


def test_working_context_employee_scoped(backend: PersonalMemoryBackend) -> None:
    scenario = ALL_SCENARIOS[3]()  # pre_interaction_context
    for item in scenario.corpus:
        backend.ingest(item, employee_id=scenario.employee_id)

    ctx = backend.working_context(
        employee_id=scenario.employee_id,
        task=scenario.query,
    )
    assert ctx.employee_id == scenario.employee_id
    assert ctx.task == scenario.query
    # In a real impl this is the relevant private context; the fake just
    # mirrors `query`. Either way every relevant_hit must be a PersonalHit.
    for hit in ctx.relevant_hits:
        assert isinstance(hit, PersonalHit)


# ---------- Entity resolution ----------


def test_resolve_entity_returns_stable_ref(backend: PersonalMemoryBackend) -> None:
    """resolve_entity must return an EntityRef with a stable entity_id shape (p_/o_)."""
    ref = backend.resolve_entity(
        ["email:sarah@northpoint.fund", "linkedin:sarah-chen"],
        employee_id="alice-vc-example",
    )
    assert isinstance(ref, EntityRef)
    assert ref.entity_id.startswith(("p_", "o_"))
    assert "email:sarah@northpoint.fund" in ref.identifiers


# ---------- Adapter-specific path safety ----------


@pytest.mark.parametrize(
    "bad_employee_id",
    ["../escape", "/tmp/escape", ".hidden", "foo/bar", "", "bad\x00id"],
)
def test_mempalace_rejects_unsafe_employee_ids_before_creating_paths(
    mempalace_backend: PersonalMemoryBackend,
    tmp_path,  # type: ignore[no-untyped-def]
    bad_employee_id: str,
) -> None:
    item = ALL_SCENARIOS[0]().corpus[0]

    with pytest.raises(ValueError):
        mempalace_backend.ingest(item, employee_id=bad_employee_id)

    assert not (tmp_path / "personal").exists()
