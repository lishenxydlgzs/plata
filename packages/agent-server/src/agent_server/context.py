"""Conversation context management backed by SQLite."""

import os
from pathlib import Path

import aiosqlite

_default_db_dir = "/home/lishenxydlgzs/data/agent-server"
DB_DIR = Path(os.environ.get("DB_DIR", _default_db_dir))
DB_PATH = DB_DIR / "conversations.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);
"""

CONTEXT_LIMIT = 5


class ConversationDB:
    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(DB_PATH)
        await self._db.executescript(SCHEMA)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def get_history(self, conversation_id: str, limit: int = CONTEXT_LIMIT) -> list[dict]:
        assert self._db
        cursor = await self._db.execute(
            """
            SELECT role, text FROM (
                SELECT role, text, created_at FROM messages
                ORDER BY created_at DESC
                LIMIT ?
            ) ORDER BY created_at ASC
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [{"role": row[0], "text": row[1]} for row in rows]

    async def save_turn(self, conversation_id: str, user_text: str, reply_text: str) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO conversations (id, last_active_at) VALUES (?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(id) DO UPDATE SET last_active_at = CURRENT_TIMESTAMP",
            (conversation_id,),
        )
        await self._db.execute(
            "INSERT INTO messages (conversation_id, role, text) VALUES (?, 'user', ?)",
            (conversation_id, user_text),
        )
        await self._db.execute(
            "INSERT INTO messages (conversation_id, role, text) VALUES (?, 'model', ?)",
            (conversation_id, reply_text),
        )
        await self._db.commit()
