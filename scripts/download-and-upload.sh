#!/usr/bin/env bash
# Download audio from YouTube (single video or playlist) and upload to the robot.
# Usage:
#   ./scripts/download-and-upload.sh <youtube-url>               # download + upload
#   ./scripts/download-and-upload.sh --download-only <url>       # download to local dir only
#   ./scripts/download-and-upload.sh --upload-only <local-dir>   # upload previously downloaded files
#
# Requires: yt-dlp (brew install yt-dlp), ffmpeg

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
DEFAULT_DOWNLOAD_DIR="$WORKSPACE_ROOT/.downloads"

do_download() {
  local url="$1"
  local dest="$2"
  mkdir -p "$dest"

  echo "=== Downloading from YouTube ==="
  echo "Destination: $dest"
  yt-dlp -x --audio-format mp3 \
    --output "$dest/%(title)s.%(ext)s" \
    --no-overwrites \
    "$url"

  local count
  count=$(find "$dest" -type f -name "*.mp3" | wc -l | tr -d ' ')
  echo "Downloaded $count file(s) to $dest"

  if [ "$count" -eq 0 ]; then
    echo "No files downloaded."
    return 1
  fi
}

do_upload() {
  local src="$1"
  echo ""
  echo "=== Uploading to robot ==="
  "$SCRIPT_DIR/upload-media.sh" "$src"
}

# Parse arguments
MODE="both"
if [[ "${1:-}" == "--download-only" ]]; then
  MODE="download"
  shift
elif [[ "${1:-}" == "--upload-only" ]]; then
  MODE="upload"
  shift
fi

if [ $# -lt 1 ]; then
  echo "Usage:"
  echo "  $0 <youtube-url>               # download + upload"
  echo "  $0 --download-only <url>        # download to .downloads/ only"
  echo "  $0 --upload-only <local-dir>    # upload a local directory"
  exit 1
fi

case "$MODE" in
  download)
    do_download "$1" "$DEFAULT_DOWNLOAD_DIR"
    echo ""
    echo "To upload later: $0 --upload-only $DEFAULT_DOWNLOAD_DIR"
    ;;
  upload)
    do_upload "$1"
    ;;
  both)
    DOWNLOAD_DIR=$(mktemp -d)
    trap 'rm -rf "$DOWNLOAD_DIR"' EXIT
    do_download "$1" "$DOWNLOAD_DIR"
    do_upload "$DOWNLOAD_DIR"
    ;;
esac
