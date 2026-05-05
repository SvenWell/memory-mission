"""CLI subapp for eval capture inspection + replay.

Wired into the main CLI (``python -m memory_mission eval ...``).
Subcommands:

- ``status`` — print per-tool capture counts + last captured timestamp.
- ``list`` — list recent captures with id, tool, time, latency, signature.
- ``replay`` — replay captured ``mm_query_entity`` calls at HEAD and
  diff result signatures. Reports match rate + mean latency delta.

All commands accept ``--root`` (or ``MM_ROOT`` env) and ``--user-id``
(or ``MM_USER_ID`` env) to locate the per-employee capture store.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from memory_mission.eval.captures import EvalCapturesStore, captures_path_for
from memory_mission.eval.replay import replay_captures
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.schema import validate_employee_id
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph

eval_app = typer.Typer(
    name="eval",
    help="Inspect + replay captured mm_* tool calls (BrainBench-Real-style).",
    no_args_is_help=True,
)


def _resolve_root_and_user(root: Path | None, user_id: str | None) -> tuple[Path, str]:
    """Resolve --root / --user-id, falling back to MM_ROOT / MM_USER_ID env."""
    resolved_root = root or (Path(os.environ["MM_ROOT"]) if "MM_ROOT" in os.environ else None)
    resolved_user = user_id or os.environ.get("MM_USER_ID") or os.environ.get("MM_PROFILE")
    if resolved_root is None:
        typer.echo("error: --root not set and MM_ROOT env var missing", err=True)
        raise typer.Exit(code=2)
    if not resolved_user:
        typer.echo("error: --user-id not set and MM_USER_ID env var missing", err=True)
        raise typer.Exit(code=2)
    validate_employee_id(resolved_user)
    return resolved_root.expanduser(), resolved_user


def _open_store(root: Path, user_id: str) -> EvalCapturesStore:
    path = captures_path_for(root=root, user_id=user_id)
    if not path.exists():
        typer.echo(f"no eval captures found at {path}", err=True)
        typer.echo(
            "run with MM_CONTRIBUTOR_MODE=1 against the individual MCP server to start capturing",
            err=True,
        )
        raise typer.Exit(code=1)
    return EvalCapturesStore(path)


@eval_app.command()
def status(
    root: Annotated[Path | None, typer.Option("--root", help="Memory Mission root.")] = None,
    user_id: Annotated[str | None, typer.Option("--user-id", help="Employee id.")] = None,
) -> None:
    """Show per-tool capture counts + last captured timestamp."""
    resolved_root, resolved_user = _resolve_root_and_user(root, user_id)
    store = _open_store(resolved_root, resolved_user)
    try:
        stats = store.stats()
    finally:
        store.close()

    typer.echo(f"User:  {resolved_user}")
    typer.echo(f"Path:  {captures_path_for(root=resolved_root, user_id=resolved_user)}")
    typer.echo(f"Total: {stats['total']}")
    last = stats.get("last_captured_at")
    if last:
        typer.echo(f"Last:  {last}")
    if stats["per_tool"]:
        typer.echo("Per-tool counts:")
        for tool, count in sorted(stats["per_tool"].items()):
            typer.echo(f"  {tool}: {count}")


@eval_app.command(name="list")
def list_captures(
    root: Annotated[Path | None, typer.Option("--root", help="Memory Mission root.")] = None,
    user_id: Annotated[str | None, typer.Option("--user-id", help="Employee id.")] = None,
    tool: Annotated[str | None, typer.Option("--tool", help="Filter by tool name.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max number of captures to print.")] = 20,
) -> None:
    """List recent captures (id, tool, time, latency, signature prefix)."""
    resolved_root, resolved_user = _resolve_root_and_user(root, user_id)
    store = _open_store(resolved_root, resolved_user)
    try:
        captures = store.list_captures(tool_name=tool, limit=limit)
    finally:
        store.close()

    if not captures:
        typer.echo("no captures matched")
        return

    typer.echo(f"{'id':>6}  {'tool':<24} {'when':<26} {'lat_ms':>6}  signature")
    for c in captures:
        sig_prefix = c.result_signature[:12]
        latency = str(c.latency_ms) if c.latency_ms is not None else "-"
        when = c.captured_at.isoformat()
        typer.echo(f"{c.capture_id:>6}  {c.tool_name:<24} {when:<26} {latency:>6}  {sig_prefix}")


@eval_app.command()
def replay(
    root: Annotated[Path | None, typer.Option("--root", help="Memory Mission root.")] = None,
    user_id: Annotated[str | None, typer.Option("--user-id", help="Employee id.")] = None,
    tool: Annotated[
        str | None,
        typer.Option(
            "--tool",
            help="Tool name to replay (default mm_query_entity — others not yet replayable).",
        ),
    ] = "mm_query_entity",
    limit: Annotated[int, typer.Option("--limit", help="Max captures to replay.")] = 50,
    show_diffs: Annotated[
        bool,
        typer.Option(
            "--show-diffs/--no-show-diffs",
            help="Print each capture id that differs from stored signature.",
        ),
    ] = False,
) -> None:
    """Replay captured tool calls at HEAD and diff against stored signatures."""
    resolved_root, resolved_user = _resolve_root_and_user(root, user_id)
    store = _open_store(resolved_root, resolved_user)

    identity = LocalIdentityResolver(resolved_root / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=resolved_root,
        employee_id=resolved_user,
        identity_resolver=identity,
    )
    try:
        result = replay_captures(store=store, kg=kg, tool_name=tool, limit=limit)
    finally:
        store.close()
        kg.close()

    typer.echo(result.summary())
    if show_diffs:
        diffs = [c for c in result.cases if not c.matched and not c.skipped]
        if diffs:
            typer.echo("\nCaptures that differ:")
            for case in diffs:
                typer.echo(
                    f"  capture_id={case.capture_id} tool={case.tool_name} "
                    f"old={case.old_signature[:12]} new={(case.new_signature or '')[:12]}"
                )
