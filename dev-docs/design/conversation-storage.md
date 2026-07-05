# Conversation Storage Design

## Goal

Persist all conversations to a local SQLite database so that:
1. History survives server restarts and redeploys
2. Conversations can be queried later (e.g. "what did the kids ask yesterday?")
3. Multi-turn context is maintained across sessions

## Schema

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,  -- 'user' or 'model'
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
```

## Database location

`/home/lishenxydlgzs/data/agent-server/conversations.db`

Outside the project directory so rsync `--delete` won't touch it. Configurable via `DB_DIR` environment variable.

## Context loading

When a request arrives for a conversation_id:
1. If the conversation is not in memory, load the last 5 messages from DB
2. Use those messages as conversation history for the LLM call
3. After generating a reply, persist both the user message and the model reply to DB
4. Update `last_active_at` on the conversation row

## Write path

For each `/conversation` request:
1. INSERT into `conversations` if new (ON CONFLICT ignore)
2. INSERT user message (role='user')
3. Call LLM
4. INSERT model reply (role='model')
5. UPDATE `conversations.last_active_at`

Steps 2 and 4-5 happen in a single transaction after the reply is generated.

## Context store changes

Replace the in-memory-only `ContextStore` with a `ConversationDB` class that:
- Opens the SQLite connection on startup (creates tables if not exists)
- Exposes `get_history(conversation_id, limit=5) -> list[dict]`
- Exposes `save_turn(conversation_id, user_text, reply_text)`
- Uses `aiosqlite` for async compatibility with FastAPI

## Dependencies

- `aiosqlite` (add to pyproject.toml)

## Migration

No migration tooling needed — the server creates tables on first run via `CREATE TABLE IF NOT EXISTS`.
