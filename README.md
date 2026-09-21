# Plata

<img src="assets/brand/plata-icon.png" alt="Plata: a teal P-shaped speech bubble with friendly, smiling robot eyes" width="160" />

A friendly robot conversation companion for kids, powered by Gemini and Home Assistant.

Plata lives on a Raspberry Pi connected to a [Home Assistant Voice PE](https://www.home-assistant.io/voice-pe/) device. Children talk to it naturally — it listens via wake word, transcribes speech, generates a contextual response, and speaks back.

## Brand philosophy

**Play comes first. Learning comes along.**

Play is children's primary task of childhood. As parents, we teach them along the way. The name **Plata** carries this idea: **Pla**y, **T**each, **A**long. We use “Plata” in everyday writing; “PlaTA” highlights the meaning behind the name.

Plata is a friendly companion for playful discovery, with parents guiding learning along the way. Its conversations should welcome curiosity, imagination, and exploration, letting learning grow naturally out of play.

Our [icon](assets/brand/plata-icon.png) is a soft, P-shaped speech bubble with happy robot eyes. It brings together Plata's name, conversation, and a cute, welcoming personality for children.

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

## Household guidance documents

Plata can use family values, communication preferences, educational philosophy,
or other household guidance. Write each document in Markdown; headings and lists
are preserved. Keep private documents outside this repository.

Deploying the application does **not** import your documents. Import them separately
into the running server's private database. Active documents are included in the
system prompt sent to the configured Gemini API on each chat request.

### Import a document

From the repository root, with the backend running:

```bash
python3 scripts/import-guidance.py /path/to/private/family-values.md \
  --server http://localhost:8200 \
  --key family-values \
  --title 'Our family values' \
  --instructions 'Use when discussing relationships, choices, and responsibilities. Explain relevant principles warmly and offer a practical next step when helpful.'
```

For a Pi installation, replace `http://localhost:8200` with `http://<pi-ip>:8200`.
The file path refers to a file on the computer running the import command.
No server restart is needed.

- `--key` identifies the document across revisions. Reuse it when updating.
- `--title` is its display title.
- `--instructions` explains **how Plata should apply** the document. The Markdown
  contains the guidance itself. Omitting this option uses general warm, practical
  application instructions.

To add another kind of guidance, run the same command with its own file, key,
title, and instructions—for example, `learning-approach` and “Our learning approach.”
Each document can contain up to 20,000 characters; all active documents are included
in the prompt, so keep them focused.

### Verify setup

Inspect active documents through the private API:

```bash
curl -fsS http://localhost:8200/api/guidance-documents
```

This returns the full private document content. An empty list means no documents
are active. After import, ask Plata “What are our family values?” or a question
about the guidance you supplied.

### Update or deactivate

Edit the local Markdown and rerun the import command with the **same key**, title,
and desired instructions. Each import creates a new revision and deactivates the
previous one; existing event links retain their original document revision.
Editing the local file alone does not update Plata.

Add `--inactive` to the import command to save a disabled revision and deactivate
that document. To reactivate it, import again without `--inactive`. Deactivation
retains historical versions, which can be inspected with:

```bash
curl -fsS 'http://localhost:8200/api/guidance-documents?include_history=true'
```

### Guidance and event logging

Guidance works independently of learning and behavior logging. Both logging types
are enabled by default. For example, to retain learning memory while disabling
behavior logging:

```bash
curl -fsS -X PUT http://localhost:8200/api/memory-settings \
  -H 'Content-Type: application/json' \
  -d '{"behavior_logging": false, "learning_logging": true}'
```

Disabling a type stops new event records and retrieval for that type; it does not
delete earlier events or disable ordinary conversation transcripts. Guidance stays
active. Existing legacy family values are used only when no guidance documents
are active.

See [Household guidance and learning memory](dev-docs/design/household-guidance-learning.md)
for the ontology and API details.

## Home Assistant integration setup

The custom integration registers Plata as a conversation agent in Home Assistant, bridging the voice pipeline to the agent server.

### Install the component

Copy the custom component to your HA config directory:

```bash
cp -r packages/ha-integration/custom_components/kids_robot /path/to/homeassistant/custom_components/
```

Or use the deploy script which handles this automatically:

```bash
./scripts/deploy.sh --ha
```

### Configure in HA

1. Restart Home Assistant
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for "Kids Robot"
4. Enter the configuration:
   - **Backend URL** — `http://<pi-ip>:8200` (default: `http://localhost:8200`)
   - **Timeout** — seconds to wait for a response (default: 10)
   - **Default mode** — conversation mode (default: chat)
5. The integration validates connectivity by calling the `/health` endpoint

### Assign to a voice assistant

1. Go to **Settings → Voice Assistants**
2. Select your assistant (or create a new one)
3. Set **Conversation agent** to "Kids Robot"
4. Assign the assistant to your Voice PE device under **Settings → Devices → Home Assistant Voice → Configure**

### How it works

```
User speaks → Voice PE → HA STT (Faster Whisper) → Kids Robot conversation agent
    → HTTP POST to agent server /conversation → Gemini generates reply
    → reply text returned to HA → TTS (Piper) → Voice PE speaker
```

The integration forwards the transcribed text along with a conversation ID to the agent server and returns the reply text for TTS synthesis.

## Adding media files

Plata can play audio files on command. Drop files into the media directory on the Pi:

```bash
scp bedtime_music.mp3 \
  lishenxydlgzs@192.168.68.60:/home/lishenxydlgzs/homeassistant/media/kids_robot/
```

No restart needed — the server scans the folder on each request. The filename becomes the title used for matching (e.g., `bedtime_music.mp3` → "Bedtime Music"), so name files descriptively. Supported formats: `.mp3`, `.mp4`, `.wav`, `.ogg`, `.flac`, `.m4a`.

See [DEVELOPING.md](./DEVELOPING.md#adding-songs-or-media-files) for more details.

## License

Private project.


## Reflective Log book

Open `/graph` and select **Log book**. Browse past notes on the left and chat on
the right. Describe an experience, add a thought, ask a question, or ask Plata to
update or remove a note. Plata chooses the title and organization through note
tools and replies briefly. There are no title/content forms or separate save steps.

**Your words** contains exact quotations checked against preserved user messages.
Organized observations, author reflections, Plata suggestions, and parking-lot
questions remain distinct. Earlier versions and source conversations are retained.
A failed turn saves the message and offers Retry; retrying does not duplicate it.
Deleting a note removes it from the active view and conversation memory; asking to
restore it recovers it. New conversation does not create an empty note.

Notes default to **Parents only**. Explicitly ask in chat to share a note with
family conversations when appropriate. Only relevant organized observations and
author reflections are retrieved; model suggestions and raw chat are excluded.
Management endpoints use the existing private-server trust boundary. The feature
does not add parent authentication. People/topics/guidance connections reference
existing ontology entities; notes do not silently replace household guidance.

Chat APIs: GET/POST `/api/logbook/sessions`, GET `/api/logbook/sessions/{id}`,
POST `/api/logbook/sessions/{id}/messages` with `text`, unique `request_id`, and
optional `selected_note_id`. Messages are limited to 8,000 characters and a
conversation to 40,000 characters. A new conversation can access existing notes.
Legacy `/api/journals` entry/organization endpoints remain compatible.
See [Log book design](dev-docs/design/logbook.md).
