# Plata

A friendly robot conversation companion for kids, powered by Gemini and Home Assistant.

Plata lives on a Raspberry Pi connected to a [Home Assistant Voice PE](https://www.home-assistant.io/voice-pe/) device. Children talk to it naturally — it listens via wake word, transcribes speech, generates a contextual response, and speaks back.

## Architecture

```
Voice PE (ESPHome)  ──audio──▶  Home Assistant (STT/TTS)
                                       │
                                  conversation
                                       │
                                       ▼
                               Agent Server (FastAPI)
                                       │
                                   Gemini API
```

- **Voice PE** — hardware device with microphone, speaker, and wake word detection
- **Home Assistant** — orchestrates the voice pipeline (STT → conversation agent → TTS)
- **Agent Server** — custom conversation backend that calls Gemini with a child-friendly system prompt
- **SQLite** — persists all conversations for context continuity across sessions

## Packages

| Package | Description |
|---------|-------------|
| `packages/agent-server/` | FastAPI conversation backend (Python 3.11+) |
| `packages/ha-integration/` | Home Assistant custom conversation agent component |
| `scripts/` | Deployment and utility scripts |
| `dev-docs/` | Requirements and design documents |

## Quick start

### Prerequisites

- Raspberry Pi with Python 3.11+
- Home Assistant instance with Voice PE configured
- Gemini API key (free tier works)

### Setup

```bash
# Clone
git clone https://github.com/lishenxydlgzs/plata.git
cd plata

# Create virtualenv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e "packages/agent-server[dev]"

# Configure
cp .env.example .env
# Edit .env with your GOOGLE_AI_STUDIO_API_KEY
```

### Run locally

```bash
source .venv/bin/activate
python -m agent_server  # starts on 0.0.0.0:8200
```

### Deploy to Pi

```bash
./scripts/deploy.sh        # sync + restart agent server
./scripts/deploy.sh --ha   # also update HA integration
```

### Run tests

```bash
source .venv/bin/activate
cd packages/agent-server && python -m pytest tests/ -v
```

## Configuration

Environment variables (set in `.env`):

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_AI_STUDIO_API_KEY` | Gemini API key | (required) |
| `LOG_DIR` | Log file directory | `/home/lishenxydlgzs/logs/agent-server` |
| `DB_DIR` | SQLite database directory | `/home/lishenxydlgzs/data/agent-server` |

## License

Private project.
