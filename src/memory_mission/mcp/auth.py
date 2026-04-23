"""MCP client manifest — enumerates which employees may connect + their scopes.

The manifest is the firm's list of MCP-eligible employees. Format:

    # firm/mcp_clients.yaml
    alice@acme.com:
      scopes: [read, propose, review]
    bob@acme.com:
      scopes: [read, propose]
    carol@acme.com:
      scopes: [read]

Unknown employees fail closed — the server refuses to start for an
employee_id absent from the manifest.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class Scope(StrEnum):
    """Capability granted to an MCP client.

    ``READ`` covers every non-mutating tool (query, get_page, search,
    get_entity, get_triples, compile_agent_context, check_coherence).
    ``PROPOSE`` adds create_proposal + list_proposals. ``REVIEW`` adds
    approve / reject / reopen + merge_entities + sql_query_readonly —
    it's the reviewer tier, gated behind explicit firm policy.
    """

    READ = "read"
    PROPOSE = "propose"
    REVIEW = "review"


class ClientEntry(BaseModel):
    """One employee permitted to run an MCP server process."""

    model_config = ConfigDict(frozen=True)

    employee_id: str
    scopes: frozenset[Scope]


class AuthError(Exception):
    """Raised when an employee is not in the manifest or lacks a required scope.

    Keeps the caller-visible message generic ("insufficient scope" /
    "not authorized") to avoid leaking the firm's scope taxonomy via
    enumeration — a low-privilege caller probing with different
    target_scope values should not learn which scope names exist.
    Audit-log consumers can read the structured ``employee_id`` /
    ``required_scope`` attributes to record the full detail.
    """

    def __init__(
        self,
        message: str = "insufficient scope",
        *,
        employee_id: str | None = None,
        required_scope: str | None = None,
    ) -> None:
        super().__init__(message)
        self.employee_id = employee_id
        self.required_scope = required_scope


class _NoDupSafeLoader(yaml.SafeLoader):
    """SafeLoader subclass that rejects duplicate mapping keys.

    Default PyYAML silently keeps the last occurrence on duplicate keys —
    an operator editing the manifest by merge could introduce a second,
    permissive entry for the same employee and not notice. Reject
    duplicates so the bug surfaces at load time.
    """


def _no_dup_construct_mapping(
    loader: _NoDupSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in MCP client manifest")
        seen.add(key)
    mapping: dict[Any, Any] = loader.construct_mapping(node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_dup_construct_mapping,
)


def _normalize_employee_id(employee_id: str) -> str:
    """Return the NFKC-normalized form of ``employee_id``.

    Unicode normalization collapses visually-identical compatibility
    characters (fullwidth ``a`` → ``a``, compat ligatures, etc.). We
    require manifest and runtime callers to use the canonical NFKC
    form so a homoglyph or fullwidth attacker can't smuggle a second
    entry that resolves to the same rendered glyph as a legitimate
    employee but sits at a different Python string key.
    """
    return unicodedata.normalize("NFKC", employee_id)


def load_manifest(path: Path) -> dict[str, ClientEntry]:
    """Load and validate the MCP client manifest from a YAML file.

    Resolves symlinks and verifies the target lives inside the manifest's
    parent directory — a symlink-swap attack that points the manifest at
    some other file elsewhere on disk would otherwise load attacker-
    controlled client entries. Rejects employee_id values that aren't
    already in NFKC canonical form.
    """
    if not path.exists():
        raise FileNotFoundError(f"MCP client manifest not found: {path}")

    real_path = path.resolve()
    real_parent = path.parent.resolve()
    if not real_path.is_relative_to(real_parent):
        raise ValueError(
            f"MCP client manifest {path} resolves outside its parent directory "
            "(possible symlink attack) — refusing to load"
        )

    raw_text = real_path.read_text(encoding="utf-8")
    data: Any = yaml.load(raw_text, Loader=_NoDupSafeLoader)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a YAML mapping at top level, got {type(data).__name__}")

    result: dict[str, ClientEntry] = {}
    for employee_id, fields in data.items():
        if not isinstance(employee_id, str) or not employee_id:
            raise ValueError(f"employee_id must be a non-empty string, got {employee_id!r}")
        if _normalize_employee_id(employee_id) != employee_id:
            raise ValueError(
                f"employee_id {employee_id!r} must be in Unicode NFKC form — "
                "homoglyph or fullwidth variants are rejected"
            )
        if not isinstance(fields, dict):
            raise ValueError(
                f"entry for {employee_id!r} must be a mapping, got {type(fields).__name__}"
            )
        scopes_raw = fields.get("scopes", [])
        if not isinstance(scopes_raw, list):
            raise ValueError(f"scopes for {employee_id!r} must be a list")
        scopes: set[Scope] = set()
        for s in scopes_raw:
            if not isinstance(s, str):
                raise ValueError(f"scope for {employee_id!r} must be a string, got {s!r}")
            try:
                scopes.add(Scope(s))
            except ValueError as exc:
                valid = ", ".join(sc.value for sc in Scope)
                raise ValueError(
                    f"unknown scope {s!r} for {employee_id!r}; valid scopes: {valid}"
                ) from exc
        result[employee_id] = ClientEntry(employee_id=employee_id, scopes=frozenset(scopes))

    return result


def resolve_employee(
    manifest: dict[str, ClientEntry],
    employee_id: str,
) -> ClientEntry:
    """Return the manifest entry for ``employee_id`` or raise ``AuthError``.

    Rejects non-NFKC input before lookup so homoglyph / fullwidth
    variants can't match a legitimate manifest key via the visual
    collision alone. Must match byte-for-byte after normalization.
    """
    if _normalize_employee_id(employee_id) != employee_id:
        raise AuthError("not authorized", employee_id=employee_id)
    entry = manifest.get(employee_id)
    if entry is None:
        raise AuthError("not authorized", employee_id=employee_id)
    return entry


def require_scope(entry: ClientEntry, scope: Scope) -> None:
    """Raise ``AuthError`` if ``entry`` is missing ``scope``."""
    if scope not in entry.scopes:
        raise AuthError(
            "insufficient scope",
            employee_id=entry.employee_id,
            required_scope=scope.value,
        )
