# Requirements

## Agent Server

### Implemented
- [x] Agent-classified song metadata — cached batched LLM tags/topics/entities, graph associations, reviewable uncertainty, and retry without redownloading
- [x] Curated CC playlist imports — classify titles by subject/cycle/week, review metadata in Jobs, and replace the legacy catalog while retaining backup audio
- [x] Background jobs UI — manage quality improvement and playlist cadence, pause/run controls, persistent execution history and logs
- [x] Scheduled YouTube playlist audio imports — persistent cadence, one folder per playlist, incremental MP3 downloads and job status
- [x] Reflective Log book — notes browser and ongoing reflective conversation with optional CRUD tools, validated verbatim quotes, preserved messages/revisions, ontology links, and family-only retrieval

- [x] POST /conversation endpoint — accepts JSON with text, conversation_id, language, source, device_id, satellite_id, timestamp
- [x] POST /hardware/button endpoint — hardware event entry point
- [x] GET /health endpoint — returns status and version
- [x] GET /status endpoint — returns running state and active conversation count
- [x] Structured response format — reply_text, mode, continue_conversation, actions
- [x] Single-mode context-aware design — LLM adapts tone (teaching, play, chat, admin) based on conversation context
- [x] Multi-turn conversation context — tracks turns per conversation_id, persisted in SQLite
- [x] Graceful error fallback — returns child-friendly apology on failure
- [x] LLM integration (Gemini 3.1 Flash Lite) — generates real responses with child-friendly system prompt
- [x] LLM fallback chain — retries temporary Gemini availability failures with alternate configured models
- [x] Response brevity enforcement — system prompt constrains replies to 1-3 short sentences
- [x] Catalog-based media playback commands — LLM selector chooses playable audio from metadata and stop audio uses response actions
- [x] Playlist playback — LLM can pick multiple tracks; HA integration plays them sequentially using state-change detection
- [x] Timers — “set a timer for X” schedules a HA timer and plays a bundled chime on expiry
- [x] Knowledge graph (ontology) — tracks conversation topics and media as linked entities, feeds memory context into system prompt
- [x] Fact extraction with provenance — LLM extracts explicit user statements as structured facts, stored with confidence scores and linked to source messages
- [x] Long-term memory — system prompt includes known facts and recency-weighted topics across all sessions
- [x] Knowledge graph review UI — visualize graph and persist parent-directed maintenance chats
- [x] Graph record corrections — review messages and household events, preserve correction provenance, and show retryable server errors
- [x] Resumable playlist playback — LLM interprets playback requests and selects resume/next-week/restart actions using persisted song progress
- [x] Household guidance documents — preserve Markdown and immutable content revisions, with application instructions separate from content
- [x] Behavior event logging — record reports and model interpretations separately, link guidance revisions, and append repair/review updates
- [x] Learning memory — record session starts and reported practice/recall/mastery, linked to stable people and topics
- [x] Relevant household memory retrieval — retrieve a child's topic history before encouragement and isolate conversation history by conversation ID
- [x] Independent memory settings — enable behavior and learning logging separately from household guidance
- [x] Legacy family values and kid events — retain existing records and APIs, with legacy guidance as fallback

### Planned
- [ ] Child-safe content filtering — block inappropriate responses
- [ ] Deterministic script logic — handle specific commands without LLM (e.g. repeat last answer)
- [ ] Home Assistant action bridge — trigger HA scripts/automations via REST API
- [x] Logging and observability — rotating file logs at /home/lishenxydlgzs/logs/agent-server/
- [x] Conversation storage — SQLite database persists all messages, loads last 5 as context

## Home Assistant Integration

### Implemented

- [x] Custom conversation agent entity — registered as selectable agent in HA Voice Assistants
- [x] Config flow UI — configure backend URL, timeout, default mode
- [x] Backend health check during setup — validates server is reachable
- [x] HTTP bridge to agent server — forwards text, returns speech response
- [x] Error handling — fallback response if agent server is unreachable or returns error
- [x] Media action handling — execute allowlisted media_player service calls on the Voice PE

### Planned

- [ ] Retry policy — configurable retry on transient failures
- [ ] Diagnostic logging level configuration
- [ ] Pass satellite_id/device_id from HA context when available
- [ ] General action handling — execute non-media HA service calls returned in response actions array

## Hardware Integration

### Planned

- [ ] GPIO button support — initiate interaction, change mode, repeat last answer
- [ ] LED status — idle/listening/thinking/speaking states
- [ ] Button event routing to agent server via /hardware/button endpoint

## Infrastructure

### Implemented

- [x] Monorepo structure — packages/agent-server, packages/ha-integration, scripts, dev-docs
- [x] Sync script — rsync workspace to Pi (scripts/sync-to-robot.sh)
- [x] Deploy script — sync + restart server, optionally update HA (scripts/deploy.sh)
- [x] Media directory provisioning — HA deploy creates `/home/lishenxydlgzs/homeassistant/media/kids_robot`
- [x] Test suite — pytest with async httpx client against FastAPI app

### Planned

- [ ] Systemd service — auto-start agent server on Pi boot
- [ ] Deploy hook — auto-copy HA integration on deploy --ha

## React and Strands Migration

### Implemented

- [x] Polished responsive React workspace — accessible navigation and complete loading, empty, success, and error states
- [x] CopilotKit conversations over AG-UI — real Strands streaming, server-owned history, safe retries, and persisted tool outcomes
- [x] Logbook feature parity — note tools, verbatim quotes, revisions, visibility, selected-note context, and graph connections
- [x] Knowledge review feature parity — graph visualization, filters, maintenance conversations, corrections, and action results
- [x] Household management screens — people, guidance revisions, learning/behavior records, review updates, and memory settings
- [x] Jobs feature parity — playlist imports, schedule/pause/run controls, history pagination, and execution logs
- [x] Strands voice orchestration — Gemini configuration/fallback and unchanged Home Assistant, media, playlist, and timer contracts
- [x] Migration verification — backend regressions, frontend checks/build, AG-UI integration tests, and desktop/mobile browser checks
- [x] Self-hosted frontend deployment — asset build/sync, private data preservation, setup and rollback documentation
