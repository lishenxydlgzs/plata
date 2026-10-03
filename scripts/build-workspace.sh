#!/usr/bin/env bash
# Produce static React assets locally; the robot does not need Node.js.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major >= 23)) { console.error("Use Node.js 20.19+ or 22.12+ to build the workspace."); process.exit(1); }'
npm ci --prefix "$WORKSPACE_ROOT/packages/web-ui"
npm run check --prefix "$WORKSPACE_ROOT/packages/web-ui"
npm run test --prefix "$WORKSPACE_ROOT/packages/web-ui"
npm run build --prefix "$WORKSPACE_ROOT/packages/web-ui"
