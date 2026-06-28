#!/usr/bin/env bash
# Sync workspace content to the remote robot server.
# Usage: ./scripts/sync-to-robot.sh [--dry-run]

set -euo pipefail

REMOTE_DEST="lishenxydlgzs@192.168.68.58:/home/lishenxydlgzs/agent-server"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"

EXCLUDES=(
    --exclude='.git'
    --exclude='__pycache__'
    --exclude='*.pyc'
    --exclude='.venv'
    --exclude='node_modules'
    --exclude='.mypy_cache'
    --exclude='.pytest_cache'
    --exclude='*.egg-info'
    --exclude='.ruff_cache'
)

DRY_RUN=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN="--dry-run"
    echo "=== DRY RUN ==="
fi

echo "Syncing $WORKSPACE_ROOT -> $REMOTE_DEST"

rsync -avz --delete \
    "${EXCLUDES[@]}" \
    $DRY_RUN \
    "$WORKSPACE_ROOT/" \
    "$REMOTE_DEST/"

echo "Done."
