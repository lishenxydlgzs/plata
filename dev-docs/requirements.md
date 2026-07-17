# Requirements

## Agent Server

### Implemented

- [x] POST /conversation endpoint — accepts JSON with text, conversation_id, language, source, device_id, satellite_id, timestamp
- [x] POST /hardware/button endpoint — hardware event entry point
- [x] GET /health endpoint — returns status and version
- [x] GET /status endpoint — returns running state and active conversation count
- [x] Structured response format — reply_text, mode, continue_conversation, actions
- [x] Single-mode context-aware design — LLM adapts tone (teaching, play, chat, admin) based on conversation context
- [x] Multi-turn conversation context — tracks turns per conversation_id, persisted in SQLite
- [x] Graceful error fallback — returns child-friendly apology on failure
- [x] LLM integration (Gemini 3.1 Flash Lite) — generates real responses with child-friendly system prompt
- [x] Response brevity enforcement — system prompt constrains replies to 1-3 short sentences
- [x] Catalog-based media playback commands — LLM selector chooses playable audio from metadata and stop audio uses response actions

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
