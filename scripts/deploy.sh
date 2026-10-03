#!/usr/bin/env bash
# Deploy to the robot Pi: sync, restart agent server, and optionally update HA integration.
# Usage:
#   ./scripts/deploy.sh           # sync + restart agent server only
#   ./scripts/deploy.sh --ha      # also update HA integration and restart Home Assistant

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
REMOTE_HOME="/home/$REMOTE_USER"
REMOTE_AGENT="$REMOTE_HOME/agent-server"
REMOTE_HA_COMPONENTS="$REMOTE_HOME/homeassistant/custom_components"
REMOTE_HA_MEDIA="$REMOTE_HOME/homeassistant/media/kids_robot"

UPDATE_HA=false
if [[ "${1:-}" == "--ha" ]]; then
    UPDATE_HA=true
fi

echo "=== Building browser workspace ==="
"$SCRIPT_DIR/build-workspace.sh"

echo "=== Saving previous application release ==="
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
ssh "$REMOTE" bash -s -- "$RELEASE_ID" <<'BACKUP'
set -euo pipefail
umask 077
release_dir="$HOME/agent-server-releases/$1"
mkdir -p "$release_dir"
cd "$HOME/agent-server"
.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
tar --exclude='./.venv' --exclude='./.git' --exclude='./.env*' \
    --exclude='./data' --exclude='./logs' --exclude='./media' \
    --exclude='./.downloads' --exclude='./classic_conversion_cycle3' \
    --exclude='node_modules' --exclude='__pycache__' \
    --exclude='*.db' --exclude='*.db-*' --exclude='*.sqlite3*' \
    -czf "$release_dir/application.tar.gz" .
.venv/bin/pip freeze > "$release_dir/python-packages.txt"
tar -czf "$release_dir/venv.tar.gz" .venv
if [ -f .env ]; then cp .env "$release_dir/environment.env"; fi
.venv/bin/python - "$release_dir" <<'PYBACKUP'
import os, sqlite3, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.cwd() / '.env')
source = Path(os.getenv('DB_DIR', './data'))
target = Path(sys.argv[1]) / 'databases'
target.mkdir(mode=0o700)
count = 0
for path in source.iterdir():
    if path.suffix not in {'.db', '.sqlite3'} or not path.is_file():
        continue
    with sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True) as original:
        with sqlite3.connect(target / path.name) as backup:
            original.backup(backup)
            assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    count += 1
print(f'Private SQLite snapshots verified: {count}')
PYBACKUP
tar -tzf "$release_dir/application.tar.gz" >/dev/null
tar -tzf "$release_dir/venv.tar.gz" >/dev/null
echo "Rollback release saved: $release_dir"
BACKUP

rollback_on_failure() {
    trap - ERR
    echo "Deployment failed. Restoring release $RELEASE_ID."
    "$SCRIPT_DIR/rollback.sh" "$RELEASE_ID" || echo "Automatic rollback failed; run scripts/rollback.sh $RELEASE_ID manually."
    exit 1
}
trap rollback_on_failure ERR

echo "=== Syncing workspace ==="
"$SCRIPT_DIR/sync-to-robot.sh"

echo "=== Installing packages ==="
ssh "$REMOTE" bash -s <<INSTALL
set -euo pipefail
cd $REMOTE_AGENT
source .venv/bin/activate
pip install -e packages/ontology -e packages/agent-server --quiet
pip check
INSTALL

echo "=== Restarting agent server ==="
ssh "$REMOTE" bash -s <<RESTART
set -euo pipefail
pkill -f 'python -m agent_server' || true
sleep 2
cd $REMOTE_AGENT
source .venv/bin/activate
nohup python -m agent_server > /dev/null 2>&1 &
disown
for i in 1 2 3 4 5 6; do
    sleep 2
    if curl -sf --connect-timeout 2 --max-time 3 http://127.0.0.1:8200/health > /dev/null; then
        echo "Agent server: OK"
        exit 0
    fi
done
echo "Agent server: FAILED to start — check $REMOTE_HOME/logs/agent-server/agent-server.log"
exit 1
RESTART

trap - ERR

if [ "$UPDATE_HA" = true ]; then
    echo "=== Updating HA integration ==="
    ssh "$REMOTE" "mkdir -p $REMOTE_HA_MEDIA"
    ssh "$REMOTE" "cp -r $REMOTE_AGENT/packages/ha-integration/custom_components/kids_robot $REMOTE_HA_COMPONENTS/"
    ssh "$REMOTE" "cp $REMOTE_AGENT/packages/ha-integration/custom_components/kids_robot/assets/timer.wav $REMOTE_HA_MEDIA/timer.wav"

    echo "=== Restarting Home Assistant ==="
    ssh "$REMOTE" "docker restart homeassistant"
    echo "Home Assistant restarting (takes ~30s to come back up)"
fi

echo "=== Deploy complete ==="
