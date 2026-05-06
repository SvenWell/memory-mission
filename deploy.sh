#!/usr/bin/env bash
# Production deploy: pull the production branch and restart Hermes.
#
# Run as root from /root/memory-mission on the production VPS.
# The VPS is pinned to the `production` branch on SvenWell/memory-mission.
# Updating production = merging into it on GitHub, then running this script.
#
# This script is idempotent — safe to re-run.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

if [ "$(git rev-parse --abbrev-ref HEAD)" != "production" ]; then
  echo "ERROR: not on the production branch (HEAD = $(git rev-parse --abbrev-ref HEAD))." >&2
  echo "Run: git checkout production" >&2
  exit 1
fi

echo "==> Fetching production..."
git fetch origin production

echo "==> Fast-forwarding to origin/production..."
git pull --ff-only origin production

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

echo "==> Restarting Hermes (systemctl --user restart hermes-gateway)..."
systemctl --user restart hermes-gateway

echo "==> Done. Verify with:"
echo "      systemctl --user status hermes-gateway"
echo "      tail -f /root/.hermes/logs/mcp-stderr.log"
