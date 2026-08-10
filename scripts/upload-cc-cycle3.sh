#!/usr/bin/env bash
# Upload CC Cycle 3 weekly playlists to the robot, preserving directory structure.
# Usage: ./scripts/upload-cc-cycle3.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment
if [ -f "$WORKSPACE_ROOT/.env" ]; then
  set -a; source "$WORKSPACE_ROOT/.env"; set +a
fi

REMOTE_USER="${REMOTE_USER:?Set REMOTE_USER in .env}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST in .env}"
REMOTE="$REMOTE_USER@$REMOTE_HOST"
REMOTE_MEDIA="/home/$REMOTE_USER/homeassistant/media/kids_robot"

LOCAL_SRC="$WORKSPACE_ROOT/classic_conversion_cycle3"

if [ ! -d "$LOCAL_SRC" ]; then
  echo "Error: $LOCAL_SRC does not exist"
  exit 1
fi

echo "=== Uploading CC Cycle 3 playlists ==="
echo "Source: $LOCAL_SRC"
echo "Destination: $REMOTE:$REMOTE_MEDIA/cc_cycle3/"

# Create remote directory structure
ssh "$REMOTE" "mkdir -p $REMOTE_MEDIA/cc_cycle3"

# Rsync preserving directory structure, normalizing filenames with spaces→underscores
rsync -avz --progress \
  --exclude='.DS_Store' \
  "$LOCAL_SRC/" \
  "$REMOTE:$REMOTE_MEDIA/cc_cycle3/"

# Normalize filenames on remote (spaces to underscores, strip special chars)
echo ""
echo "=== Normalizing filenames ==="
ssh "$REMOTE" bash -s <<'NORMALIZE'
cd /home/lishenxydlgzs/homeassistant/media/kids_robot/cc_cycle3
find . -type f -name "*.mp3" | while IFS= read -r file; do
  dir=$(dirname "$file")
  base=$(basename "$file")
  # Replace spaces with underscores, remove special chars except dots and underscores
  normalized=$(echo "$base" | sed 's/ /_/g' | sed 's/[^A-Za-z0-9._-]//g')
  if [ "$base" != "$normalized" ]; then
    mv "$dir/$base" "$dir/$normalized" 2>/dev/null || true
  fi
done
echo "Done normalizing."
NORMALIZE

# Count files
echo ""
total=$(ssh "$REMOTE" "find $REMOTE_MEDIA/cc_cycle3 -type f -name '*.mp3' | wc -l")
echo "=== Upload complete: $total files ==="
