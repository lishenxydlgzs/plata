# React workspace operations

The browser uses React and CopilotKit with a direct, self-managed AG-UI client.
FastAPI serves the static build and runs Strands with the existing Gemini
configuration. No Copilot Cloud account, Node server, or AWS credentials are needed.
See [the migration design](react-agent-workspace.md) for data boundaries and tools.

## Local setup

Use Python 3.11+ and Node 20.19+ or 22.12+ (Node 24 is recommended). From the
repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/ontology -e 'packages/agent-server[dev]'
cp .env.example .env  # only on first setup; retain an existing .env
./scripts/build-workspace.sh
python -m agent_server
```

Set the existing Gemini API key in `.env`. Open `http://localhost:8200/`.
`UI_DIST` can override the default `packages/web-ui/dist` directory. Build before
starting FastAPI: the static mount is registered during application import.
For frontend development, run `npm run dev --prefix packages/web-ui` with the
backend on port 8200; Vite proxies `/api`, `/media`, and `/health`.

The default `/`, `/graph`, and `/jobs` routes open the built workspace. Old
`#graph` and `#journal` bookmarks remain supported. Source-only installations
without a build retain the old pages. `/legacy/graph` and `/legacy/jobs` always
provide the previous UI. Existing REST endpoints remain available.

Data remains in the configured `DB_DIR`; no database export or replacement is
part of this migration. The additive `graph_review_turns` table records browser
review requests and atomic mutation receipts in the ontology database. Existing
review messages remain readable. Logbook uses its existing versioned records.
Use one server process, as before, for the background worker and SQLite services.

## Verification

```bash
source .venv/bin/activate
(cd packages/agent-server && python -m pytest tests/ -v)
(cd packages/ontology && python -m pytest tests/ -v)
npm run check --prefix packages/web-ui
npm run test --prefix packages/web-ui
npm run build --prefix packages/web-ui
bash -n scripts/build-workspace.sh scripts/deploy.sh scripts/sync-to-robot.sh
```

Backend integration tests use real Strands orchestration, native tools and the
AG-UI adapter with deterministic model events. Gemini transport tests mock
Google SDK responses, including fallback and partial-stream failures. They do
not consume Gemini quota. Use `/health` or `/status` for connectivity checks.

Browser acceptance uses disposable synthetic data: create a private note with a
verbatim source, inspect knowledge and start a review, add guidance and a person,
edit/pause a job, and inspect desktop/mobile layouts. Do not use deployed family
data in screenshots or fixtures.

## Deployment

Production rollout is separate from implementation and local verification.
When rollout is authorized, run `./scripts/deploy.sh`. Use `--ha` only for changes
to the HA integration. The script:

1. Installs locked frontend dependencies, checks TypeScript, runs frontend tests,
   and produces static assets locally. The Pi does not need Node.
2. Checks remote Python 3.11+ and creates a timestamped application-only archive
   under `~/agent-server-releases/`, with the complete Python environment, package
   versions, configuration, and verified private SQLite snapshots.
3. Syncs source and built assets while excluding databases, environment files,
   media, logs and local dependencies; installs backend packages and restarts.
4. Checks `/health`. Inspect `/`, `/workspace/`, `/jobs`, and one saved browser
   conversation after rollout. Exercise voice once within the Gemini quota.

The application archive excludes private data; separate SQLite snapshots and
configuration copies stay in the protected release directory. Keep regular
private backups as well, outside this repository. Reverse
proxies must pass SSE without buffering; the backend sends
`X-Accel-Buffering: no`. Keep the service on the existing trusted household network.
Browser administration retains the previous server trust boundary.

## Application rollback

If the new UI alone fails, use `/legacy/graph` and `/legacy/jobs` while diagnosing.
For a full rollback, run `./scripts/rollback.sh TIMESTAMP` from the build computer,
using the timestamp printed by deployment. It restores code and dependencies
while retaining current databases. The manual alternative below is for older
archives without a saved environment. On the robot:

```bash
cd ~/agent-server
release_dir="$HOME/agent-server-releases/REPLACE_WITH_TIMESTAMP"
test -f "$release_dir/application.tar.gz"
restore_dir="$(mktemp -d)"
tar -xzf "$release_dir/application.tar.gz" -C "$restore_dir"
pkill -f 'python -m agent_server' || true
rsync -a --delete \
  --exclude='.venv/' --exclude='.git/' --exclude='.env*' \
  --exclude='data/' --exclude='logs/' --exclude='media/' \
  --exclude='.downloads/' --exclude='classic_conversion_cycle3/' \
  --exclude='node_modules/' --exclude='__pycache__/' \
  --exclude='*.db' --exclude='*.db-*' --exclude='*.sqlite3*' \
  "$restore_dir/" "$HOME/agent-server/"
source .venv/bin/activate
# Restore recorded dependencies, excluding editable source installs.
python - "$release_dir/python-packages.txt" "$restore_dir/requirements.txt" <<'PY'
import sys
from pathlib import Path
lines = Path(sys.argv[1]).read_text().splitlines()
Path(sys.argv[2]).write_text('\n'.join(
    line for line in lines if line and not line.startswith(('#', '-e '))
) + '\n')
PY
pip install -r "$restore_dir/requirements.txt"
pip install -e packages/ontology -e packages/agent-server
nohup python -m agent_server > /dev/null 2>&1 &
sleep 3
curl -fsS http://127.0.0.1:8200/health
```

Review the selected release path before running `rsync --delete`. Its excludes
preserve current operational data and configuration. Do not restore old databases
as an application rollback: doing so discards new conversations. Older code can
ignore the additive browser receipt table, but its legacy graph chat will not
show new browser-only review replies; the records and corrections remain intact.
Keep the release and temporary restore directory until health and voice checks
succeed, then remove the temporary directory. Rollback is documented, not executed
against the production robot during development.

## Known limits

- The CopilotKit dependency produces a large initial JavaScript bundle; Vite emits
  a chunk-size warning. Assets are self-hosted; first load can be slower on a
  constrained network. This does not affect voice requests.
- Read-only assistant replies stream immediately. Replies following mutations are
  released after the atomic database commit, so a failed write cannot appear as
  a successful save. Tool progress still streams.
- Existing session history is authoritative on the server. A failed/cancelled turn
  remains retryable with the same request ID; successful replays do not reapply
  changes. Start a new conversation when its bounded context fills.
- The graph drawing caps visible nodes at 120; the filtered record list remains
  available for larger graphs. Job history retains 100 runs and 500 logs per run,
  matching the existing API.
- Local tests do not establish real-provider latency or Pi performance. A bounded
  live Gemini/HA smoke check remains part of the separately authorized rollout.

## Automated rollback support (2026-10-03)

Deployment now saves the complete `.venv`, configuration, and verified SQLite
backup snapshots in the private robot release directory (mode 0700), alongside
the application archive. Database snapshots use SQLite's online backup API and
integrity checks. They remain on the robot and are never synced into Git.

Failures during sync, dependency installation or the startup health check trigger
application/environment rollback automatically. To request the same rollback:

```bash
./scripts/rollback.sh YYYYMMDDTHHMMSSZ
```

Use the timestamp printed by deployment. This restores the previous code and
Python environment without downloading dependencies and **retains current
operational databases**. The failed environment is retained with the release for
diagnosis. Private database snapshots are disaster-recovery copies, not part of
routine rollback; restore them only with the service stopped and after deciding
how to retain data written since the snapshot. The earlier manual instructions
are a fallback for release archives created before this support existed.

## Production rollout — 2026-10-03

Branch: `codex/react-strands-workspace`. Baseline rollback tag:
`rollback/pre-react-strands-20261003` (commit `e4e2ccb`). Migration commit:
`302ee91`; rollback safeguards: `44c2377`.

The original production release, environment, configuration and three verified
SQLite snapshots are retained privately on the robot under
`~/agent-server-releases/20261003T230315Z`. To restore the pre-migration application:

```bash
./scripts/rollback.sh 20261003T230315Z
```

Agent-server-only deployment passed health, static route and entry-asset checks.
Live Gemini checks on the robot used disposable databases and disabled workers:
voice response (1.19 s), note creation plus idempotent replay (1.51 s), timer
response (0.82 s), and graph correction retaining its original source (1.17 s).
No timer or media action was executed on physical devices by these tests.

The actual LAN browser also completed a separate, clearly labeled deployment
connectivity conversation with a live streamed reply. Its persisted receipt was
verified and it made no note mutations. Production database integrity checks
passed; scheduled-job state/history remained visible in the deployed UI. The
backend regression suite passed all 106 tests. Deployment builds run the five
frontend tests, TypeScript check and Vite build. A low-severity transitive
DOMPurify advisory found during rollout was fixed in the lockfile; npm audit
reported zero remaining advisories. Physical microphone/STT/TTS testing still
requires a person at the Voice PE. No HA integration files were changed.
