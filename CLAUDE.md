# Kids Robot Conversation Agent

## Project structure

Monorepo with packages in `packages/`:
- `packages/agent-server/` — FastAPI conversation backend (Python 3.11+)
- `packages/ha-integration/` — Home Assistant custom conversation agent component
- `scripts/` — deployment and utility scripts
- `dev-docs/` — requirements and design documents
- `dev-docs/design/` — design docs for new features

## Dev workflow

1. **Local development** — edit code in this workspace
2. **Run tests** — `source .venv/bin/activate && cd packages/agent-server && python -m pytest tests/ -v`
3. **Deploy** — `./scripts/deploy.sh` (see below)

## Deploy

```bash
./scripts/deploy.sh        # sync + restart agent server only
./scripts/deploy.sh --ha   # also update HA integration + restart Home Assistant container
```

Use `--ha` only when you've changed files in `packages/ha-integration/`. Agent server changes (the common case) only need the default deploy.

## Remote target

- Host: `lishenxydlgzs@192.168.68.60`
- Server path: `/home/lishenxydlgzs/agent-server`
- Server port: 8200 (8123 is used by Home Assistant)
- HA config: `/home/lishenxydlgzs/homeassistant/` (mounted as `/config` in the `homeassistant` container)
- HA custom components: `/home/lishenxydlgzs/homeassistant/custom_components/kids_robot/`

## Build & test

```bash
source .venv/bin/activate
pip install -e "packages/agent-server[dev]"
cd packages/agent-server && python -m pytest tests/ -v
```

## Run server locally

```bash
source .venv/bin/activate
python -m agent_server  # starts on 0.0.0.0:8200
```

## LLM rate limits

The Gemini API key is on a free tier with a 15 RPM limit. When testing or debugging, minimize calls to the `/conversation` endpoint. Use `/health` or `/status` for connectivity checks.

## Design docs

Place design documents in `dev-docs/design/`. One doc per feature, written before implementation.

## Requirements tracking

`dev-docs/requirements.md` is the source of truth for feature status. When developing:
- Move items from "Planned" to "Implemented" (with `[x]`) as they are completed
- Add new requirements to the appropriate "Planned" section when requested
- Keep descriptions concise — one line per item
