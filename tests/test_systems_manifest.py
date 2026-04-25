"""Contract tests for ``SystemsManifest`` + ``map_visibility`` (P2).

The manifest is per-firm config that drives capability binding +
fail-closed visibility mapping. These tests assert:

- valid YAML round-trips through the loader
- invalid YAML / malformed structure rejects loudly
- VisibilityRule requires at least one matcher (no empty rules)
- map_visibility evaluates rules in order (first match wins)
- map_visibility fails closed when no rule matches and no default is set
- map_visibility uses operator-set ``default_visibility`` when present
- looking up an unbound role raises ``KeyError``
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.ingestion.roles import ConnectorRole
from memory_mission.ingestion.systems_manifest import (
    RoleBinding,
    SystemsManifest,
    VisibilityMappingError,
    VisibilityRule,
    load_systems_manifest,
    map_visibility,
)

_VALID_YAML = """
firm_id: northpoint
bindings:
  email:
    app: gmail
    target_plane: personal
    visibility_rules:
      - if_label: external-shared
        scope: external-shared
      - if_label: lp-only
        scope: lp-only
    default_visibility: null
  transcript:
    app: granola
    target_plane: personal
    default_visibility: partner-only
  document:
    app: drive
    target_plane: firm
    visibility_rules:
      - if_field:
          drive_anyone: true
        scope: public
    default_visibility: client-confidential
"""


# ---------- Loader ----------


def test_load_minimal_manifest_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "systems.yaml"
    p.write_text(_VALID_YAML, encoding="utf-8")

    manifest = load_systems_manifest(p)

    assert manifest.firm_id == "northpoint"
    assert ConnectorRole.EMAIL in manifest.bindings
    assert ConnectorRole.TRANSCRIPT in manifest.bindings
    assert ConnectorRole.DOCUMENT in manifest.bindings
    assert manifest.binding(ConnectorRole.EMAIL).app == "gmail"
    assert manifest.binding(ConnectorRole.EMAIL).target_plane == "personal"
    assert manifest.binding(ConnectorRole.TRANSCRIPT).default_visibility == "partner-only"
    assert manifest.binding(ConnectorRole.DOCUMENT).target_plane == "firm"


def test_load_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    p = tmp_path / "systems.yaml"
    p.write_text("- not a mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_systems_manifest(p)


def test_load_rejects_missing_firm_id(tmp_path: Path) -> None:
    p = tmp_path / "systems.yaml"
    p.write_text("bindings: {}\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_systems_manifest(p)


def test_load_rejects_unknown_role_key(tmp_path: Path) -> None:
    p = tmp_path / "systems.yaml"
    p.write_text(
        "firm_id: x\nbindings:\n  bogus_role:\n    app: gmail\n    target_plane: personal\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_systems_manifest(p)


def test_load_rejects_unknown_target_plane(tmp_path: Path) -> None:
    p = tmp_path / "systems.yaml"
    p.write_text(
        "firm_id: x\nbindings:\n  email:\n    app: gmail\n    target_plane: nope\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_systems_manifest(p)


# ---------- VisibilityRule validation ----------


def test_visibility_rule_requires_at_least_one_matcher() -> None:
    with pytest.raises(ValidationError, match="if_label or if_field"):
        VisibilityRule(scope="public")


def test_visibility_rule_requires_non_empty_scope() -> None:
    with pytest.raises(ValidationError):
        VisibilityRule(if_label="x", scope="")


def test_role_binding_rejects_empty_app() -> None:
    with pytest.raises(ValidationError):
        RoleBinding(app="", target_plane="personal")


def test_systems_manifest_rejects_empty_firm_id() -> None:
    with pytest.raises(ValidationError):
        SystemsManifest(firm_id="", bindings={})


# ---------- binding() lookup ----------


def test_binding_unbound_role_raises_key_error() -> None:
    manifest = SystemsManifest(firm_id="x", bindings={})
    with pytest.raises(KeyError, match="email"):
        manifest.binding(ConnectorRole.EMAIL)


# ---------- map_visibility ----------


def _manifest() -> SystemsManifest:
    return SystemsManifest(
        firm_id="northpoint",
        bindings={
            ConnectorRole.EMAIL: RoleBinding(
                app="gmail",
                target_plane="personal",
                visibility_rules=(
                    VisibilityRule(if_label="external-shared", scope="external-shared"),
                    VisibilityRule(if_label="lp-only", scope="lp-only"),
                ),
                default_visibility=None,
            ),
            ConnectorRole.TRANSCRIPT: RoleBinding(
                app="granola",
                target_plane="personal",
                default_visibility="partner-only",
            ),
            ConnectorRole.DOCUMENT: RoleBinding(
                app="drive",
                target_plane="firm",
                visibility_rules=(VisibilityRule(if_field={"drive_anyone": True}, scope="public"),),
                default_visibility="client-confidential",
            ),
        },
    )


def test_map_visibility_matches_label_rule() -> None:
    scope = map_visibility(
        {"labels": ["external-shared", "important"]},
        role=ConnectorRole.EMAIL,
        manifest=_manifest(),
    )
    assert scope == "external-shared"


def test_map_visibility_first_match_wins() -> None:
    scope = map_visibility(
        {"labels": ["external-shared", "lp-only"]},
        role=ConnectorRole.EMAIL,
        manifest=_manifest(),
    )
    assert scope == "external-shared"


def test_map_visibility_matches_field_rule() -> None:
    scope = map_visibility(
        {"drive_anyone": True, "owners": ["x@y.z"]},
        role=ConnectorRole.DOCUMENT,
        manifest=_manifest(),
    )
    assert scope == "public"


def test_map_visibility_fail_closed_when_no_default() -> None:
    with pytest.raises(VisibilityMappingError, match="fail-closed"):
        map_visibility(
            {"labels": ["personal-only"]},
            role=ConnectorRole.EMAIL,
            manifest=_manifest(),
        )


def test_map_visibility_uses_default_when_set() -> None:
    scope = map_visibility(
        {"owners": ["x@y.z"]},
        role=ConnectorRole.DOCUMENT,
        manifest=_manifest(),
    )
    assert scope == "client-confidential"


def test_map_visibility_uses_default_when_no_rules_at_all() -> None:
    scope = map_visibility(
        {},
        role=ConnectorRole.TRANSCRIPT,
        manifest=_manifest(),
    )
    assert scope == "partner-only"


def test_map_visibility_unbound_role_raises_key_error() -> None:
    manifest = SystemsManifest(firm_id="x", bindings={})
    with pytest.raises(KeyError):
        map_visibility({}, role=ConnectorRole.CALENDAR, manifest=manifest)


def test_map_visibility_label_must_be_list() -> None:
    """A scalar `labels` field shouldn't accidentally satisfy a label rule."""
    with pytest.raises(VisibilityMappingError):
        map_visibility(
            {"labels": "external-shared"},  # str, not list — must not match
            role=ConnectorRole.EMAIL,
            manifest=_manifest(),
        )


def test_map_visibility_field_rule_requires_all_pairs() -> None:
    rule_manifest = SystemsManifest(
        firm_id="x",
        bindings={
            ConnectorRole.DOCUMENT: RoleBinding(
                app="drive",
                target_plane="firm",
                visibility_rules=(
                    VisibilityRule(
                        if_field={"drive_anyone": True, "domain": "northpoint.fund"},
                        scope="public",
                    ),
                ),
                default_visibility=None,
            ),
        },
    )
    # only one of two pairs matches → no match → fail-closed
    with pytest.raises(VisibilityMappingError):
        map_visibility(
            {"drive_anyone": True, "domain": "other.fund"},
            role=ConnectorRole.DOCUMENT,
            manifest=rule_manifest,
        )
