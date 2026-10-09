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
| `packages/web-ui/` | React/CopilotKit workspace with AG-UI streaming to Strands |
| `packages/ha-integration/` | Home Assistant custom conversation agent component |
| `scripts/` | Deployment and utility scripts |
| `dev-docs/` | Requirements and design documents |

## Quick start

### Prerequisites

- Raspberry Pi with Python 3.11+
- Home Assistant instance with Voice PE configured
- Gemini API key (free tier works)
- Node.js 20.19+ or 22.12+ on the build computer (not required on the Pi)

### Setup

```bash
# Clone
git clone https://github.com/lishenxydlgzs/plata.git
cd plata

# Create virtualenv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/ontology -e "packages/agent-server[dev]"

# Configure
cp .env.example .env
# Edit .env with your GOOGLE_AI_STUDIO_API_KEY
```

### Run locally

```bash
source .venv/bin/activate
./scripts/build-workspace.sh
python -m agent_server  # starts on 0.0.0.0:8200
```

Open `http://localhost:8200/` for the React household workspace. Conversations use
CopilotKit and AG-UI with a self-hosted Strands backend and the existing Gemini
configuration. See [workspace setup, verification and rollback](dev-docs/design/workspace-operations.md).

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

See [Testing Plata through Home Assistant](dev-docs/testing/home-assistant-voice.md)
for remote text, recorded-audio pipeline, and satellite playback tests.

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

Open `/workspace/#logbook`. Browse past notes on the left and chat on
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

### Scheduled YouTube playlist imports

Install the agent dependencies as usual, and install **FFmpeg/ffprobe** plus a
supported **Deno** runtime on the server (see the
[yt-dlp runtime prerequisites](https://github.com/yt-dlp/yt-dlp#dependencies)).
The Python dependency includes yt-dlp and its default extras. Keep yt-dlp updated
when YouTube extraction changes. Set `MEDIA_DIR` to the same local media directory
used by Home Assistant. The service needs write access there.

Use the **Media playlist sync** section of the server's `/docs` page, or:

```bash
curl -X POST http://localhost:8200/media/sync-jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID","name":"Sample playlist","interval_hours":24}'
```

The first sync is queued immediately and begins within one minute when the worker
is free. Each playlist gets a folder such as
`Sample_playlist_PLAYLIST_ID/`, with title-and-video-ID MP3 filenames. Playback
uses filename order. Sync is additive: it skips downloaded videos, retries failed
ones, and keeps audio for videos removed from YouTube. Use public or unlisted
playlists containing media you have permission to download.

- `GET /media/sync-jobs`: persisted state, last/next run (Unix timestamps), count
  imported on the last attempt, and error details.
- `PUT /media/sync-jobs/{id}` with `{"interval_hours":12,"enabled":true}`:
  change cadence or pause using `enabled:false`. Pausing lets an active run finish.
- `POST /media/sync-jobs/{id}/sync`: queue an enabled job for another attempt.

Jobs persist in `DB_DIR/playlist-sync.sqlite3` (`./data` by default). Restarting
recovers interrupted jobs. Run one server process; the worker serializes downloads
to limit load on the robot. Manage jobs only from the trusted household network,
consistent with the server's existing administration APIs. Private playlists and
account cookies are not supported.

### Background jobs page

Open `/jobs` (also linked from the household workspace) to manage both the daily
knowledge quality improvement job and YouTube imports. Add playlists, edit the
repeat interval in hours, pause/resume jobs, or queue a run. Saving an interval
sets the next execution one interval from now; Run now explicitly queues work.
The maintenance job initially starts at the next server-local midnight, then
repeats every 24 hours unless changed. This is an elapsed-time interval, not a
fixed wall-clock schedule across daylight-saving changes.

Each job shows its next/last execution and retained history, including outcome,
start/end times, duration, manual/scheduled trigger, summary, and per-run logs.
The page refreshes every five seconds while preserving edits. History retains the
newest 100 runs per job and the latest 500 bounded log entries per run. It starts
with this feature; previous executions cannot be reconstructed. Settings and
history share `DB_DIR/playlist-sync.sqlite3`; interrupted runs are marked on
restart and their jobs requeued. All job types share one worker.

The management API is `/api/jobs`, with `PUT /{id}` for interval/enabled settings,
`POST /{id}/run` to queue work, `GET /{id}/runs?before=RUN_ID` for history, and
`GET /{id}/runs/{run_id}` for logs. Existing playlist APIs remain available.
The legacy `/maintenance/run` endpoint also records history and rejects overlap.

### Talk to Plata on Telegram

You can establish the same connection through the **workspace UI** or **REST API**.
Both paths use the same stored configuration, invitations, and account permissions;
a connection created by a coding agent appears immediately in the UI.

#### Option 1: workspace UI

After building the workspace, open **Connections → Telegram** (or the Telegram
card on the Logbook home page). The owner creates a dedicated bot through the
linked official BotFather, pastes its token into the masked field, and chooses a
household person to connect. No `.env` token, terminal command, public webhook,
or router configuration is needed. Keep the home server online.

Create an invitation, then open its link or scan its QR code and tap **Start** in
Telegram. Return to the workspace to confirm the actual Telegram account. For a
second family member, select or add their person profile and share a separate
invitation; they never need the bot token or workspace access. Invitations expire
in 15 minutes and can be used once. All account connections require confirmation.
After a normal text message receives a reply, the workspace marks that account's
first conversation complete.

#### Option 2: REST API (coding agents and scripts)

Run the server, then call its API from the trusted home network or through a
private tunnel. No browser session or cookies are required. Every `/api/telegram`
request needs `X-Plata-Workspace: 1`; JSON requests also need
`Content-Type: application/json`. The workspace header is a CSRF/client-intent
check, **not an authentication secret**. The API inherits the workspace's local
network administrative access model and must not be exposed publicly. Non-browser
clients can omit `Origin`; browser requests must still be same-origin.

The household owner first creates a dedicated bot with the official
[BotFather](https://t.me/BotFather). A coding agent can perform the following
administrative steps on the owner's behalf. The recipient still needs to open
the invitation and press **Start** in Telegram; the API does not bypass that step.

**1. Configure the bot.** Set the base URL for your running server:

```bash
export PLATA_URL='http://127.0.0.1:8200'
```

Use the project's activated Python environment (which includes `httpx`). This
example reads the token from a hidden terminal prompt or, for a non-interactive
coding agent, an existing private token file selected by
`PLATA_TELEGRAM_TOKEN_FILE`. Keep that file outside the repository with permissions
`0600`. Do not put the actual token in shell commands, arguments, logs, or chat.

```bash
python - <<'PYTHON'
import getpass
import os
from pathlib import Path
import httpx

secret_file = os.environ.get("PLATA_TELEGRAM_TOKEN_FILE")
token = (Path(secret_file).read_text().strip() if secret_file
         else getpass.getpass("Telegram bot token: "))
response = httpx.put(
    os.environ["PLATA_URL"].rstrip("/") + "/api/telegram/bot",
    headers={"X-Plata-Workspace": "1"},
    json={"token": token},
    timeout=180,
)
response.raise_for_status()
print(response.json())  # Configuration status; the bot token is never returned.
PYTHON
```

A successful response has `configured: true` and the bot's `username`. Configuration
validates the token and starts the connection automatically; no server restart is
needed. Reusing the same bot preserves account links. Switching to another bot
removes existing links and invitations, just as it does in the UI.

**2. Select or create a household person.** Inspect existing profiles first to avoid
creating duplicates:

```bash
curl --fail-with-body --silent --show-error "$PLATA_URL/api/people"
```

If the intended person is missing, create one and retain the returned `id`:

```bash
curl --fail-with-body --silent --show-error \
  -X POST "$PLATA_URL/api/people" \
  -H 'Content-Type: application/json' \
  --data '{"name":"Sample Person","aliases":[]}'
```

**3. Create an invitation.** Replace `PERSON_ID_FROM_RESPONSE` below with the actual
profile ID. Acknowledge the shared-memory policy described below on the owner's
behalf only when that policy is understood:

```bash
curl --fail-with-body --silent --show-error \
  -X POST "$PLATA_URL/api/telegram/invitations" \
  -H 'X-Plata-Workspace: 1' \
  -H 'Content-Type: application/json' \
  --data '{"person_id":"PERSON_ID_FROM_RESPONSE","shared_memory_acknowledged":true}'
```

The response contains `id` (the invitation ID), `url` (the Telegram deep link), and
`expires` (Unix seconds). Give the link to the intended person to open and tap
**Start**. It is single-use and expires after 15 minutes. Creating a new invitation
for the same profile replaces the old one. Store the link privately while needed;
status deliberately does not return it again. If it is lost, create a new invitation.

**4. Inspect and confirm the account.** Check status periodically, for example every
3 seconds, while waiting for the recipient to press Start:

```bash
curl --fail-with-body --silent --show-error \
  "$PLATA_URL/api/telegram" -H 'X-Plata-Workspace: 1'
```

Find the invitation in `invitations` by its `id`. Its `state` moves from `waiting`
to `pending`, and `user_id` and `label` identify the requesting Telegram account.
An expired invitation reports `expired` and needs replacement. Compare `user_id`
with the account ID the bot shows the recipient; a display name alone is not proof
of identity. Once the owner has authorized connecting that account, an agent can
confirm it directly through the API, without opening the workspace:

```bash
curl --fail-with-body --silent --show-error \
  -X POST "$PLATA_URL/api/telegram/invitations/INVITATION_ID_FROM_RESPONSE/approve" \
  -H 'X-Plata-Workspace: 1'
```

This returns `{"ok":true}`, consumes the invitation, creates the account binding,
and queues the welcome message. Approving before Start, after expiry, or after
consumption returns an error. If the approval response is lost, inspect `accounts`
in status before retrying; the account may already be connected.

**5. Verify the first interaction.** Have the connected person send a normal text
message to the bot. Poll the same status endpoint and inspect their entry in
`accounts`: `first_reply` changes from `null` to a Unix timestamp after the full
first reply is delivered. `error` reports transport problems, `last_poll` records
the last successful Telegram poll, and `delivery_error` on an account explains a
blocked or otherwise undeliverable reply. For a second household member, repeat
steps 2–5 with a separate profile and invitation; reuse the existing bot.

**API reference and cleanup.** Substitute returned IDs for path placeholders.
JSON errors use FastAPI's `detail` field. Missing/cross-site workspace headers return
403, invalid request bodies or an unacknowledged sharing policy return 422, and
invalid tokens, unavailable profiles, or invalid approvals return 400. A configured
bot can still have a transport error; inspect status instead of treating
`configured` as proof of a working connection.

| Method and path | Body / result |
| --- | --- |
| `GET /api/people` | List profiles and their IDs. |
| `POST /api/people` | `{"name":"Sample Person","aliases":[]}` → created profile. |
| `PUT /api/telegram/bot` | `{"token":"BOT_TOKEN"}` → token-free connection status. |
| `GET /api/telegram` | Configuration, errors, memory notice, invitations, and accounts. |
| `POST /api/telegram/invitations` | `{"person_id":"PERSON_ID","shared_memory_acknowledged":true}` → `id`, `url`, `expires`. |
| `POST /api/telegram/invitations/{id}/approve` | No body → `{"ok":true}`. |
| `DELETE /api/telegram/invitations/{id}` | Cancel an unused invitation or reject a pending request. |
| `DELETE /api/telegram/accounts/{user_id}` | Revoke an account and clear its queued work/output. |
| `DELETE /api/telegram/bot` | Disconnect the bot and remove all bindings/invitations. |
| `POST /api/telegram/qr` | `{"url":"INVITATION_URL"}` → locally generated SVG, not JSON. |

For example, revoke one account while leaving the household bot connected:

```bash
curl --fail-with-body --silent --show-error \
  -X DELETE "$PLATA_URL/api/telegram/accounts/TELEGRAM_USER_ID_FROM_STATUS" \
  -H 'X-Plata-Workspace: 1'
```

Use these endpoints rather than editing SQLite or running a second Telegram
`getUpdates` consumer, which would conflict with Plata's poller. API-created
connections have the same privacy, retry, and revocation behavior as UI-created
connections. Disconnecting does not erase existing household memories.

#### Shared behavior, privacy, and recovery

Each account has its own chat history. Messages and extracted facts/events can
enter shared household memory and appear in the household workspace. This is
**not private journal storage**. The setup and bot welcome explain this before
conversation. `/help` repeats these details; `/new` starts fresh chat history while
retaining shared memory; `/disconnect` revokes Telegram access. Voice messages,
timers, and home-device control are not supported in this initial version.

The settings page inherits the existing trusted local-network workspace access
model; do not expose it publicly. Tokens and transport state persist in
`DB_DIR/telegram/state.db` (mode 0600, enclosing directory 0700), excluded from Git
and deployment sync. A process lock enforces one server worker. The token is
never returned by the settings API or included in application HTTP request logs.
QR codes are generated locally. Back up this state only as private operational
data. Disconnecting an account or bot does not delete existing shared memory.

Connection errors appear on the settings page. An expired invitation needs a new
link; an unconfirmed account needs owner confirmation; a blocked bot needs to be
unblocked in Telegram. A bot already used by another webhook must be disconnected
there first, or replaced with a dedicated bot. Updating the token for the same bot
preserves accounts; switching bots removes old account links and invitations.
Telegram responses may take up to one long-poll interval (20 seconds) after owner
approval. Temporary delivery errors are retried without repeating agent work;
a network failure after Telegram accepted a reply can produce a duplicate reply.

See [the Telegram onboarding design](dev-docs/design/telegram-onboarding.md) for
state transitions, authorization boundaries, and verification coverage.

### Conversation ontology search

Robot and Telegram conversations expose a read-only native Strands tool,
`search_ontology`. The agent can search by text, browse record types, look across
multiple people, and follow returned graph links. For questions such as “What
could our children practice?”, instructions tell the agent to retrieve household
evidence before answering and distinguish reports from verified facts.

Search includes shared facts, people, learning and behavior events, guidance,
catalog records, and current family-visible notes. Parent-only notes, archived
notes, raw conversations, and superseded revisions are excluded; memory settings
still apply. Connecting a parent through Telegram does not grant access to
parent-only notes.

Search runs locally without a separate Gemini request. Returning tool results
to the model requires another model round: one search followed by an answer
normally uses two Gemini requests. Each user turn permits up to six searches and
four model rounds; model fallback attempts may add requests. Results are paginated
and text fields are bounded excerpts. Search uses literal terms, not semantic
embeddings; the agent can browse filtered records when wording differs.

See [the ontology search design](dev-docs/design/ontology-conversation-search.md).
