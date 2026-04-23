"""Enable ``python -m memory_mission.mcp`` to launch the server."""

from memory_mission.mcp.server import app

if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    app()
