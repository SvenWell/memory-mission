"""Consolidate one personal-plane user_id into another.

Addresses the multi-agent identifier-coordination gap
(``project_multi_agent_identifier_gap.md``, 2026-04-27): when two
agents pick different forms of the same logical user (``sven`` vs
``6052376253``), the substrate ends up with two parallel personal
KGs neither agent fully sees. This module's
``migrate_personal_kg`` copies all triples from one user's KG into
another's so the downstream consumer (e.g. Hermes' live runtime)
gets the union.

Idempotent: ``find_current_triple`` checks if the destination
already has the (subject, predicate, object) before re-writing,
so re-running yields stable counts. Bayesian corroboration is
NOT triggered by re-runs — the migration just preserves what was
already on the source side.

Out of scope for V1:

- **Working pages migration.** The personal-plane working pages
  live in the ``BrainEngine``, which is per-process in-memory in
  Individual mode (``mcp/individual_server.py`` /
  ``integrations/hermes_provider.py``). There's no on-disk page
  store to copy across user_ids. When/if pages persist, this
  module grows a page-copy step.
- **Identity resolver alias.** Full alias mechanism (queries against
  ``sven`` find triples written under ``6052376253``) is deferred
  to v0.2.0 — substrate-level surgery touching every read path.
  Migrate-once is the V1 fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.schema import validate_employee_id
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


@dataclass(frozen=True)
class MigrateUserIdReport:
    """Counts produced by one migration run."""

    triples_read: int = 0
    triples_written: int = 0
    triples_skipped_already_present: int = 0


def migrate_personal_kg(
    *,
    from_user_id: str,
    to_user_id: str,
    root: Path | str,
) -> MigrateUserIdReport:
    """Copy all triples from one user's personal KG into another's.

    Args:
        from_user_id: Source user_id (e.g. ``"sven"``).
        to_user_id: Destination user_id (e.g. ``"6052376253"``).
        root: Memory Mission root (e.g. ``~/.memory-mission``).

    Returns:
        ``MigrateUserIdReport`` with read / written / skipped counts.

    Raises:
        ValueError: if either user_id is path-unsafe.
        FileNotFoundError: if the source user's KG doesn't exist.
    """
    if from_user_id == to_user_id:
        raise ValueError("from_user_id and to_user_id must differ")
    validate_employee_id(from_user_id)
    validate_employee_id(to_user_id)

    root_path = Path(root).expanduser()
    src_db = root_path / "personal" / from_user_id / "personal_kg.db"
    if not src_db.is_file():
        raise FileNotFoundError(
            f"source personal KG not found at {src_db}. "
            f"Use list_personal_user_ids({root!r}) to see what's available."
        )

    # Both KGs share the firm-wide identity resolver so personal
    # entities resolve to consistent stable IDs across user namespaces.
    resolver = LocalIdentityResolver(root_path / "identity.sqlite3")
    src = PersonalKnowledgeGraph.for_employee(
        firm_root=root_path, employee_id=from_user_id, identity_resolver=resolver
    )
    dst = PersonalKnowledgeGraph.for_employee(
        firm_root=root_path, employee_id=to_user_id, identity_resolver=resolver
    )

    triples_read = 0
    triples_written = 0
    triples_skipped = 0
    try:
        for triple in src.timeline():
            triples_read += 1
            # Skip if destination already has an identical (s, p, o)
            # currently-true triple — idempotency for re-runs.
            existing = dst.find_current_triple(triple.subject, triple.predicate, triple.object)
            if existing is not None:
                triples_skipped += 1
                continue
            dst.add_triple(
                triple.subject,
                triple.predicate,
                triple.object,
                valid_from=triple.valid_from,
                valid_to=triple.valid_to,
                confidence=triple.confidence,
                source_closet=triple.source_closet,
                source_file=triple.source_file,
                tier=triple.tier,
            )
            triples_written += 1
    finally:
        src.close()
        dst.close()

    return MigrateUserIdReport(
        triples_read=triples_read,
        triples_written=triples_written,
        triples_skipped_already_present=triples_skipped,
    )


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    from memory_mission.personal_brain.discovery import list_personal_user_ids

    parser = argparse.ArgumentParser(
        description=(
            "Copy a personal KG from one user_id to another (consolidate "
            "fragmented state from multi-agent identifier drift)."
        ),
    )
    parser.add_argument("--from", dest="from_user_id", help="Source user_id")
    parser.add_argument("--to", dest="to_user_id", help="Destination user_id")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("~/.memory-mission").expanduser(),
        help="Memory Mission root. Default ~/.memory-mission",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing user_ids under the root and exit (no migration).",
    )
    args = parser.parse_args(argv)

    root_path = Path(args.root).expanduser()
    if args.list or (not args.from_user_id and not args.to_user_id):
        existing = list_personal_user_ids(root_path)
        if not existing:
            print(f"(no personal KGs found under {root_path})")
        else:
            print(f"existing user_ids under {root_path}:")
            for uid in existing:
                print(f"  - {uid}")
        return 0

    if not args.from_user_id or not args.to_user_id:
        parser.error("both --from and --to are required (or use --list)")

    report = migrate_personal_kg(
        from_user_id=args.from_user_id,
        to_user_id=args.to_user_id,
        root=root_path,
    )
    print(f"triples_read              {report.triples_read}")
    print(f"triples_written           {report.triples_written}")
    print(f"already_present_skipped   {report.triples_skipped_already_present}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())


__all__ = ["MigrateUserIdReport", "main", "migrate_personal_kg"]
