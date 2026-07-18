#!/usr/bin/env bash
# Upload audio/video files to the robot's media directory with name normalization.
# Usage:
#   ./scripts/upload-media.sh /path/to/local/folder
#   ./scripts/upload-media.sh /path/to/single-file.mp3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment
if [ -f "$WORKSPACE_ROOT/.env" ]; then
  set -a; source "$WORKSPACE_ROOT/.env"; set +a
fi

REMOTE_USER="${REMOTE_USER:-lishenxydlgzs}"
REMOTE_HOST="${REMOTE_HOST:-192.168.68.60}"
REMOTE="$REMOTE_USER@$REMOTE_HOST"
REMOTE_MEDIA="/home/$REMOTE_USER/homeassistant/media/kids_robot"
SUPPORTED_EXTENSIONS="mp3 mp4 wav ogg flac m4a"

normalize_name() {
  local filename="$1"
  local ext="${filename##*.}"
  local stem="${filename%.*}"

  # Strip non-ASCII and non-alphanumeric (keep spaces)
  stem=$(echo "$stem" | sed "s/[^A-Za-z0-9 ]//g")
  # Trim leading/trailing spaces, collapse multiple spaces
  stem=$(echo "$stem" | sed "s/^[[:space:]]*//" | sed "s/[[:space:]]*$//" | sed "s/  */ /g")
  # Replace spaces with underscores
  stem=$(echo "$stem" | sed "s/ /_/g")

  if [ -z "$stem" ]; then
    echo ""
    return
  fi

  echo "${stem}.${ext}"
}

is_supported() {
  local ext="${1##*.}"
  ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
  for supported in $SUPPORTED_EXTENSIONS; do
    if [ "$ext" = "$supported" ]; then
      return 0
    fi
  done
  return 1
}

if [ $# -lt 1 ]; then
  echo "Usage: $0 <source-dir-or-file> [source-dir-or-file ...]"
  exit 1
fi

# Collect files to upload
files=()
for src in "$@"; do
  if [ -d "$src" ]; then
    while IFS= read -r -d '' f; do
      files+=("$f")
    done < <(find "$src" -maxdepth 1 -type f -print0)
  elif [ -f "$src" ]; then
    files+=("$src")
  else
    echo "Warning: skipping '$src' (not a file or directory)"
  fi
done

if [ ${#files[@]} -eq 0 ]; then
  echo "No files found."
  exit 0
fi

# Ensure remote directory exists
ssh "$REMOTE" "mkdir -p $REMOTE_MEDIA"

uploaded=0
skipped=0

for filepath in "${files[@]}"; do
  filename=$(basename "$filepath")

  if ! is_supported "$filename"; then
    echo "SKIP (unsupported format): $filename"
    skipped=$((skipped + 1))
    continue
  fi

  normalized=$(normalize_name "$filename")
  if [ -z "$normalized" ]; then
    echo "SKIP (no usable name): $filename"
    skipped=$((skipped + 1))
    continue
  fi

  echo "UPLOAD: $filename -> $normalized"
  scp -q "$filepath" "$REMOTE:$REMOTE_MEDIA/$normalized"
  uploaded=$((uploaded + 1))
done

echo ""
echo "Done: $uploaded uploaded, $skipped skipped."
