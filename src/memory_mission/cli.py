"""CLI entry point. Run: python -m memory_mission --help"""

from __future__ import annotations

import typer

from memory_mission import __version__
from memory_mission.cli_log import log_app
from memory_mission.eval.cli import eval_app

app = typer.Typer(
    name="memory-mission",
    help="Memory Mission — enterprise AI knowledge infrastructure.",
    no_args_is_help=True,
)

app.add_typer(log_app, name="log", help="Inspect the observability audit trail.")
app.add_typer(eval_app, name="eval", help="Capture + replay mm_* tool calls.")


@app.command()
def version() -> None:
    """Print the current version."""
    typer.echo(f"memory-mission {__version__}")


@app.command()
def info() -> None:
    """Print build info and loaded configuration."""
    from memory_mission.config import get_settings

    settings = get_settings()
    typer.echo(f"memory-mission {__version__}")
    typer.echo(f"wiki_root:           {settings.wiki_root}")
    typer.echo(f"observability_root:  {settings.observability_root}")
    typer.echo(f"database_url:        {settings.database_url or '(PGLite embedded)'}")
    typer.echo(f"llm_provider:        {settings.llm_provider}")
    typer.echo(f"llm_model:           {settings.llm_model}")


if __name__ == "__main__":
    app()
