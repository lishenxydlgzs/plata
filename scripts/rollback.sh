#!/usr/bin/env bash
# Restore application code and Python environment; retain current private data.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
if [ -f "$WORKSPACE_ROOT/.env" ]; then
    set -a; source "$WORKSPACE_ROOT/.env"; set +a
fi
RELEASE_ID="${1:?Usage: scripts/rollback.sh YYYYMMDDTHHMMSSZ}"
[[ "$RELEASE_ID" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo 'Invalid release timestamp'; exit 1; }
ssh "${REMOTE_USER:?}@${REMOTE_HOST:?}" bash -s -- "$RELEASE_ID" <<'REMOTE'
set -euo pipefail
release_dir="$HOME/agent-server-releases/$1"
cd "$HOME/agent-server"
test -f "$release_dir/application.tar.gz"
test -f "$release_dir/venv.tar.gz"
tar -tzf "$release_dir/application.tar.gz" >/dev/null
tar -tzf "$release_dir/venv.tar.gz" >/dev/null
restore_dir="$(mktemp -d)"
tar -xzf "$release_dir/application.tar.gz" -C "$restore_dir"
pkill -f '^([^ ]*/)?python(3(\.[0-9]+)?)? -m agent_server$' || true
sleep 2
rsync -a --delete --exclude='.venv/' --exclude='.git/' --exclude='.env*' \
    --exclude='data/' --exclude='logs/' --exclude='media/' --exclude='.downloads/' \
    --exclude='classic_conversion_cycle3/' --exclude='node_modules/' \
    --exclude='*.db' --exclude='*.db-*' --exclude='*.sqlite3*' \
    "$restore_dir/" "$HOME/agent-server/"
# Keep the failed environment outside the application for diagnosis.
mv .venv "$release_dir/failed-venv-$(date -u +%Y%m%dT%H%M%SZ)"
tar -xzf "$release_dir/venv.tar.gz" -C "$HOME/agent-server"
nohup .venv/bin/python -m agent_server > /dev/null 2>&1 < /dev/null &
for attempt in 1 2 3 4 5 6; do
    sleep 2
    if curl -fsS --max-time 3 http://127.0.0.1:8200/health >/dev/null; then
        echo "Restored release $1; current databases retained."
        exit 0
    fi
done
echo 'Rollback health check failed; inspect server logs.' >&2
exit 1
REMOTE
