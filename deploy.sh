#!/usr/bin/env bash
# Deploy: fast-forward main and restart Hermes.
#
# Run as root from /root/memory-mission on the VPS.
# The VPS is pinned to `main` on SvenWell/memory-mission. Updating = merging
# into main on GitHub, then running this script.
#
# This script is idempotent — safe to re-run.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

if [ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]; then
  echo "ERROR: not on main (HEAD = $(git rev-parse --abbrev-ref HEAD))." >&2
  echo "Run: git checkout main" >&2
  exit 1
fi

echo "==> Fetching main..."
git fetch origin main

echo "==> Fast-forwarding to origin/main..."
git pull --ff-only origin main

# Belt-and-suspenders: ensure the launcher symlink resolves to the deploy/ path.
# The symlink is also tracked in git, so a fresh checkout already has it,
# but this catches the case where someone manually edited it.
TARGET="deploy/individual_with_mempalace.py"
LINK="individual_with_mempalace.py"
if [ ! -L "$LINK" ] || [ "$(readlink "$LINK")" != "$TARGET" ]; then
  echo "==> (re)creating launcher symlink: $LINK -> $TARGET"
  rm -f "$LINK"
  ln -s "$TARGET" "$LINK"
fi

# Refresh dependencies in case pyproject.toml moved.
if [ -d ".venv" ]; then
  echo "==> Refreshing editable install (picks up pyproject.toml changes)..."
  ./.venv/bin/pip install -e . --quiet
fi

echo "==> Restarting Hermes (systemctl --user restart hermes-gateway)..."
systemctl --user restart hermes-gateway

echo "==> Done. Verify with:"
echo "      systemctl --user status hermes-gateway"
echo "      tail -f /root/.hermes/logs/mcp-stderr.log"
