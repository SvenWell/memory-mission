"""`memory-mission log` subcommands — inspect the observability audit trail."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from memory_mission.config import get_settings
from memory_mission.observability import (
    EVENTS_FILENAME,
    ObservabilityLogger,
    serialize_event,
)

log_app = typer.Typer(no_args_is_help=True)


def _resolve_observability_root(override: Path | None) -> Path:
    if override is not None:
        return override
    return get_settings().observability_root


@log_app.command("tail")
def tail(
    firm: Annotated[str, typer.Option("--firm", help="Firm ID to tail.")],
    observability_root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            help="Override observability root directory.",
        ),
    ] = None,
    event_type: Annotated[
        str | None,
        typer.Option(
            "--event-type",
            help="Filter: extraction | promotion | retrieval | draft.",
        ),
    ] = None,
    follow: Annotated[
        bool,
        typer.Option("--follow/--no-follow", help="Follow new events as they arrive."),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max events to show.")] = 20,
) -> None:
    """Show recent events from the audit log. Pass --follow to stream."""
    root = _resolve_observability_root(observability_root)
    logger = ObservabilityLogger(observability_root=root, firm_id=firm)

    events = list(logger.read_all())
    if event_type:
        events = [e for e in events if e.event_type == event_type]

    for event in events[-limit:]:
        typer.echo(json.dumps(serialize_event(event), indent=None))

    if follow:
        for event in logger.tail():
            if event_type and event.event_type != event_type:
                continue
            typer.echo(json.dumps(serialize_event(event), indent=None))


@log_app.command("count")
def count(
    firm: Annotated[str, typer.Option("--firm", help="Firm ID to count.")],
    observability_root: Annotated[
        Path | None,
        typer.Option("--root", help="Override observability root directory."),
    ] = None,
) -> None:
    """Print total event count for a firm."""
    root = _resolve_observability_root(observability_root)
    logger = ObservabilityLogger(observability_root=root, firm_id=firm)
    typer.echo(str(logger.count()))


@log_app.command("path")
def path(
    firm: Annotated[str, typer.Option("--firm", help="Firm ID.")],
    observability_root: Annotated[
        Path | None,
        typer.Option("--root", help="Override observability root directory."),
    ] = None,
) -> None:
    """Print the filesystem path of a firm's audit log."""
    root = _resolve_observability_root(observability_root)
    typer.echo(str(root / firm / EVENTS_FILENAME))
