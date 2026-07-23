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
│   │   │   ├── media.py       # Media catalog scanning + playback actions
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

The media catalog is built dynamically by scanning this directory at runtime. No JSON config file is needed — just drop audio files with descriptive filenames.

### Naming convention

The filename becomes the title the LLM uses for matching, so name files clearly:

```text
bedtime_music.mp3       → title: "Bedtime Music"
twinkle-twinkle.mp3     → title: "Twinkle Twinkle"
wheels_on_the_bus.mp3   → title: "Wheels On The Bus"
bingo.mp4               → title: "Bingo"
```

- Use underscores or hyphens to separate words (both work)
- Avoid spaces in filenames
- Supported formats: `.mp3`, `.mp4`, `.wav`, `.ogg`, `.flac`, `.m4a`

### Upload a file

```bash
scp "twinkle_twinkle.mp3" \
  lishenxydlgzs@192.168.68.60:/home/lishenxydlgzs/homeassistant/media/kids_robot/
```

No deploy needed — the server scans the directory on each request. The new file is available immediately.

### Test a media request

```bash
ssh lishenxydlgzs@192.168.68.60 "curl -s -X POST http://127.0.0.1:8200/conversation \
  -H 'Content-Type: application/json' \
  -d '{\"text\":\"play twinkle twinkle\",\"conversation_id\":\"media-test\",\"language\":\"en\",\"source\":\"manual\"}'"
```

The response should include a `media_player.play_media` action with a `media_content_id` like:

```text
media-source://media_source/local/kids_robot/twinkle_twinkle.mp3
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

## Troubleshooting

### Bot not responding to voice at all

**Symptoms:** Speaking to the Voice PE does nothing — no acknowledgment, no response.

**Check agent server:**
```bash
curl -s http://192.168.68.60:8200/health
```
If this fails, the server is down. Restart with `./scripts/deploy.sh`.

**Check HA logs for voice pipeline activity:**
```bash
ssh lishenxydlgzs@192.168.68.60 "docker logs homeassistant --since 5m 2>&1 | grep -i 'voice\|wake\|stt\|conversation\|kids_robot'"
```

If no pipeline logs appear, the Voice PE isn't sending audio to HA. Power cycle the device (unplug 10+ seconds).

### Bot hears you but doesn't speak back

**Symptoms:** Agent server logs show incoming requests and replies, but no audio plays from the Voice PE.

**Look for speaker errors in HA logs:**
```bash
ssh lishenxydlgzs@192.168.68.60 "docker logs homeassistant --since 30m 2>&1 | grep -i '09fe19'"
```

Common indicators:
- `speaker_source.media_player took a long time for an operation` — speaker pipeline stuck
- `Error unloading entry... Config entry was never loaded!` — assist_satellite in broken state
- `i2s_audio.speaker: Driver failed to start` / `Parent bus is busy` — hardware audio driver stuck

**Fix:** Restart Home Assistant:
```bash
ssh lishenxydlgzs@192.168.68.60 "docker restart homeassistant"
```
Takes ~30s to come back up. If it persists after restart, power cycle the Voice PE.

### Agent server dies silently

The server runs via `nohup` and has no auto-restart. If it crashes (OOM, unhandled exception), it stays dead until redeployed.

**Check if running:**
```bash
ssh lishenxydlgzs@192.168.68.60 "ps aux | grep agent_server | grep -v grep"
```

**Check crash logs:**
```bash
ssh lishenxydlgzs@192.168.68.60 "tail -50 /home/lishenxydlgzs/logs/agent-server/agent-server.log"
```

**Fix:** `./scripts/deploy.sh` (syncs and restarts).

**Permanent fix:** Set up a systemd service for auto-restart on crash/reboot (see deploy docs).

## Architecture notes

**Single mode design** — The agent uses one LLM call per request with a system prompt that adapts tone based on context (child vs. parent, play vs. learn). Earlier multi-mode routing was removed in favor of letting the LLM handle tone-switching naturally.

**Cross-conversation context** — The context store loads the last 5 messages globally (across all conversation IDs) because Home Assistant creates a new conversation_id per voice session. This gives Plata continuity across sessions.

**Log rotation** — Daily rotation at midnight, 7 days retained. Logs include both incoming text and outgoing replies for debugging speech recognition and response quality.
