# Developing

## Project layout

```
plata/
├── packages/
│   ├── agent-server/          # FastAPI conversation backend
│   │   ├── src/agent_server/
│   │   │   ├── app.py         # FastAPI app, logging, lifespan
│   │   │   ├── router.py      # Request routing
│   │   │   ├── llm.py         # Gemini API client
│   │   │   ├── context.py     # SQLite conversation store
│   │   │   └── modes/
│   │   │       ├── base.py    # Abstract mode handler
│   │   │       └── chat.py    # Chat mode + system prompt
│   │   └── tests/
│   └── ha-integration/        # Home Assistant custom component
│       └── custom_components/kids_robot/
├── scripts/
│   ├── deploy.sh              # Full deploy (sync + restart + verify)
│   └── sync-to-robot.sh       # rsync workspace to Pi
├── dev-docs/
│   ├── requirements.md        # Feature tracking
│   └── design/                # Design docs (one per feature)
└── CLAUDE.md                  # AI assistant instructions
```

## Development workflow

1. Edit code locally in this workspace
2. Run tests to verify changes
3. Deploy to the Pi with `./scripts/deploy.sh`
4. Tail logs to observe behavior

## Running tests

```bash
source .venv/bin/activate
pip install -e "packages/agent-server[dev]"

# Run with env overrides for local paths
LOG_DIR=/tmp/agent-server-logs DB_DIR=/tmp/agent-server-data \
  python -m pytest packages/agent-server/tests/ -v
```

The `LOG_DIR` and `DB_DIR` overrides are needed because the defaults point to Pi paths that don't exist locally.

## Deployment

```bash
./scripts/deploy.sh        # agent server only
./scripts/deploy.sh --ha   # agent server + HA integration
```

What the deploy script does:
1. `rsync` workspace to Pi (excludes .git, .venv, __pycache__, etc.)
2. Kills the existing agent server process
3. Starts a new one via `nohup`
4. Retries health check up to 6 times (Pi takes ~10s to start)

Use `--ha` only when you've modified files in `packages/ha-integration/`.

## Adding songs or media files

Playable media files live on the Home Assistant host:

```text
/home/lishenxydlgzs/homeassistant/media/kids_robot/
```

Use simple filenames with no spaces when possible:

```text
twinkle-twinkle.mp3
wheels-on-the-bus.mp3
bingo.mp4
```

Upload a file from your local machine:

```bash
scp "twinkle-twinkle.mp3" \
  lishenxydlgzs@192.168.68.60:/home/lishenxydlgzs/homeassistant/media/kids_robot/twinkle-twinkle.mp3
```

Then add a matching entry to `packages/agent-server/src/agent_server/media_catalog.json`:

```json
{
  "id": "twinkle_twinkle",
  "title": "Twinkle Twinkle Little Star",
  "description": "A gentle children's song about a little star.",
  "aliases": ["twinkle twinkle", "little star", "twinkle twinkle little star"],
  "file": "twinkle-twinkle.mp3",
  "media_content_type": "music"
}
```

Field notes:
- `id` must be unique and stable; use lowercase snake_case.
- `title` is what Plata says back before playback.
- `description` helps the LLM choose the right file for fuzzy requests.
- `aliases` are direct phrases that should work without an LLM call.
- `file` must exactly match the filename in the HA media folder, including case.
- `media_content_type` is usually `music`; for video targets you can try `video`, but the Voice PE is primarily an audio playback device.

Deploy catalog changes with the regular agent-server deploy:

```bash
./scripts/deploy.sh
```

Use `--ha` only if you changed Home Assistant integration code. Adding media files or catalog entries does not require `--ha`.

Test the catalog entry without using the Voice PE:

```bash
ssh lishenxydlgzs@192.168.68.60 "curl -s -X POST http://127.0.0.1:8200/conversation \
  -H 'Content-Type: application/json' \
  -d '{\"text\":\"play twinkle twinkle\",\"conversation_id\":\"media-test\",\"language\":\"en\",\"source\":\"manual\"}'"
```

The response should include a `media_player.play_media` action with a `media_content_id` like:

```text
media-source://media_source/local/kids_robot/twinkle-twinkle.mp3
```

## Remote Pi details

| Item | Value |
|------|-------|
| Host | `lishenxydlgzs@192.168.68.60` |
| Server code | `/home/lishenxydlgzs/agent-server` |
| Server port | 8200 |
| Logs | `/home/lishenxydlgzs/logs/agent-server/agent-server.log` |
| Database | `/home/lishenxydlgzs/data/agent-server/conversations.db` |
| HA config | `/home/lishenxydlgzs/homeassistant/` |
| HA port | 8123 |

## Useful commands

```bash
# Tail server logs
ssh lishenxydlgzs@192.168.68.60 "tail -f /home/lishenxydlgzs/logs/agent-server/agent-server.log"

# Query conversation DB
ssh lishenxydlgzs@192.168.68.60 "sqlite3 /home/lishenxydlgzs/data/agent-server/conversations.db \
  'SELECT conversation_id, role, text, created_at FROM messages ORDER BY created_at DESC LIMIT 20;'"

# Check server health
curl http://192.168.68.60:8200/health

# Check HA container logs
ssh lishenxydlgzs@192.168.68.60 "docker logs homeassistant --tail 50"

# Test a conversation (uses 1 LLM call)
curl -X POST http://192.168.68.60:8200/conversation \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello!", "conversation_id": "dev-test"}'
```

## LLM rate limits

The Gemini API key is on a free tier with limited RPM. When testing:
- Use `/health` or `/status` for connectivity checks (no LLM call)
- Minimize calls to `/conversation` during development
- If you hit 429 errors, wait ~60s before retrying

## Adding new features

1. Write a design doc in `dev-docs/design/` before implementing
2. Implement the feature
3. Update `dev-docs/requirements.md` to reflect the new status
4. Run tests, deploy, and verify on the Pi

## Architecture notes

**Single mode design** — The agent uses one LLM call per request with a system prompt that adapts tone based on context (child vs. parent, play vs. learn). Earlier multi-mode routing was removed in favor of letting the LLM handle tone-switching naturally.

**Cross-conversation context** — The context store loads the last 5 messages globally (across all conversation IDs) because Home Assistant creates a new conversation_id per voice session. This gives Plata continuity across sessions.

**Log rotation** — Daily rotation at midnight, 7 days retained. Logs include both incoming text and outgoing replies for debugging speech recognition and response quality.
