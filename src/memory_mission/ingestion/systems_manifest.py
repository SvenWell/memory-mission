"""Per-firm capability-binding manifest + fail-closed visibility mapping.

Firms describe which concrete app fulfils each ``ConnectorRole`` and how
external visibility annotations map to firm scopes. The manifest lives
on disk as ``firm/systems.yaml``; this module is the typed loader +
runtime mapping primitive.

The mapping is **fail-closed by default**: a connector emits an item
whose ``visibility_metadata`` does not match any rule, and the binding
has no ``default_visibility``, the call raises ``VisibilityMappingError``.
The operator opts in to a fallback by setting ``default_visibility``
explicitly. There is no implicit fallback.

This is the substrate the P2 envelope helpers sit on top of: every
``NormalizedSourceItem`` gets its ``target_scope`` and ``target_plane``
from the manifest, not from connector-side guesswork.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory_mission.ingestion.roles import ConnectorRole
from memory_mission.memory.schema import Plane


class VisibilityMappingError(ValueError):
    """An item's visibility metadata did not map to a firm scope.

    Raised by ``map_visibility`` when no ``VisibilityRule`` matches the
    metadata and the binding has no ``default_visibility`` (fail-closed).
    """


class VisibilityRule(BaseModel):
    """One declarative match-rule from external visibility → firm scope.

    A rule matches if all of its set matchers match the metadata:

    - ``if_label`` matches if the string is in ``metadata["labels"]``
      (a list of strings, conventionally Gmail labels / Drive sharing
      categories / Granola visibility tags the envelope helper extracts).
    - ``if_field`` matches if every ``key: value`` pair equals
      ``metadata[key]`` (simple equality, no nested traversal).

    A rule with both matchers requires both to match. A rule must have
    at least one matcher set; the all-matchers-empty case is rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    if_label: str | None = None
    if_field: dict[str, Any] | None = None
    scope: str

    @model_validator(mode="after")
    def _has_matcher_and_scope(self) -> VisibilityRule:
        if self.if_label is None and not self.if_field:
            raise ValueError("VisibilityRule requires at least one of if_label or if_field")
        if not self.scope:
            raise ValueError("VisibilityRule.scope must be non-empty")
        return self


class RoleBinding(BaseModel):
    """One firm's binding from a logical role → concrete app + scope rules.

    ``visibility_rules`` are evaluated in order; the first matching rule
    wins. ``default_visibility`` is the operator-set fallback when no
    rule matches. ``None`` (the default) means fail-closed: an item with
    unmappable visibility is rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    app: str
    target_plane: Plane
    visibility_rules: tuple[VisibilityRule, ...] = Field(default_factory=tuple)
    default_visibility: str | None = None

    @model_validator(mode="after")
    def _validate_app_and_default(self) -> RoleBinding:
        if not self.app or not self.app.strip():
            raise ValueError("RoleBinding.app must be a non-empty string")
        if self.default_visibility is not None and not self.default_visibility:
            raise ValueError(
                "RoleBinding.default_visibility, when set, must be non-empty; "
                "use None to opt into fail-closed behavior"
            )
        return self


class SystemsManifest(BaseModel):
    """Per-firm capability binding + visibility mapping.

    Loaded from ``firm/systems.yaml``. The ``bindings`` map keys are
    ``ConnectorRole`` values (``email``, ``calendar``, ``transcript``,
    ``document``, ``workspace``). A firm may bind any subset of roles —
    unbound roles raise ``KeyError`` when looked up.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    firm_id: str
    bindings: dict[ConnectorRole, RoleBinding]

    @model_validator(mode="after")
    def _validate_firm_id(self) -> SystemsManifest:
        if not self.firm_id or not self.firm_id.strip():
            raise ValueError("SystemsManifest.firm_id must be a non-empty string")
        return self

    def binding(self, role: ConnectorRole) -> RoleBinding:
        try:
            return self.bindings[role]
        except KeyError:
            raise KeyError(
                f"role {role.value!r} has no binding in systems manifest for firm {self.firm_id!r}"
            ) from None


def load_systems_manifest(path: Path) -> SystemsManifest:
    """Read and validate ``firm/systems.yaml`` at ``path``.

    Raises ``FileNotFoundError`` if the file does not exist,
    ``yaml.YAMLError`` if the file isn't valid YAML, and
    ``pydantic.ValidationError`` if the structure doesn't satisfy
    ``SystemsManifest``.
    """
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError(
            f"systems manifest at {path} must be a YAML mapping at the top level; "
            f"got {type(raw).__name__}"
        )
    return SystemsManifest.model_validate(raw)


def map_visibility(
    visibility_metadata: Mapping[str, Any],
    *,
    role: ConnectorRole,
    manifest: SystemsManifest,
) -> str:
    """Return the firm scope for an item with this visibility metadata.

    Evaluates ``manifest.binding(role).visibility_rules`` in order and
    returns the first matching rule's ``scope``. Falls back to
    ``binding.default_visibility`` if set; otherwise raises
    ``VisibilityMappingError`` (fail-closed).
    """
    binding = manifest.binding(role)
    for rule in binding.visibility_rules:
        if _rule_matches(rule, visibility_metadata):
            return rule.scope
    if binding.default_visibility is None:
        raise VisibilityMappingError(
            f"no visibility rule matched for role={role.value!r} "
            f"under firm {manifest.firm_id!r}; "
            f"visibility_metadata={dict(visibility_metadata)!r}; "
            "default_visibility is None (fail-closed)"
        )
    return binding.default_visibility


def _rule_matches(rule: VisibilityRule, metadata: Mapping[str, Any]) -> bool:
    if rule.if_label is not None:
        labels = metadata.get("labels")
        if not isinstance(labels, list) or rule.if_label not in labels:
            return False
    if rule.if_field is not None:
        for key, expected in rule.if_field.items():
            if metadata.get(key) != expected:
                return False
    return True


__all__ = [
    "RoleBinding",
    "SystemsManifest",
    "VisibilityMappingError",
    "VisibilityRule",
    "load_systems_manifest",
    "map_visibility",
]
