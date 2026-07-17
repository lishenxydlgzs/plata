#!/usr/bin/env bash
# Deploy to the robot Pi: sync, restart agent server, and optionally update HA integration.
# Usage:
#   ./scripts/deploy.sh           # sync + restart agent server only
#   ./scripts/deploy.sh --ha      # also update HA integration and restart Home Assistant

set -euo pipefail

REMOTE="lishenxydlgzs@192.168.68.60"
REMOTE_AGENT="/home/lishenxydlgzs/agent-server"
REMOTE_HA_COMPONENTS="/home/lishenxydlgzs/homeassistant/custom_components"
REMOTE_HA_MEDIA="/home/lishenxydlgzs/homeassistant/media/kids_robot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

UPDATE_HA=false
if [[ "${1:-}" == "--ha" ]]; then
    UPDATE_HA=true
fi

echo "=== Syncing workspace ==="
"$SCRIPT_DIR/sync-to-robot.sh"

echo "=== Restarting agent server ==="
ssh "$REMOTE" bash -s <<'RESTART'
pkill -f 'python -m agent_server' || true
sleep 2
cd /home/lishenxydlgzs/agent-server
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
echo "Agent server: FAILED to start — check /home/lishenxydlgzs/logs/agent-server/agent-server.log"
exit 1
RESTART

if [ "$UPDATE_HA" = true ]; then
    echo "=== Updating HA integration ==="
    ssh "$REMOTE" "mkdir -p $REMOTE_HA_MEDIA"
    ssh "$REMOTE" "cp -r $REMOTE_AGENT/packages/ha-integration/custom_components/kids_robot $REMOTE_HA_COMPONENTS/"

    echo "=== Restarting Home Assistant ==="
    ssh "$REMOTE" "docker restart homeassistant"
    echo "Home Assistant restarting (takes ~30s to come back up)"
fi

echo "=== Deploy complete ==="
