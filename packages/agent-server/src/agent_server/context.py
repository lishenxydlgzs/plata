"""Conversation context management backed by SQLite."""

import os
import json
import uuid
from pathlib import Path

import aiosqlite

DB_DIR = Path(os.environ.get("DB_DIR", "./data"))
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

CREATE TABLE IF NOT EXISTS graph_review_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS graph_review_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES graph_review_sessions(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_graph_review_messages_session
    ON graph_review_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS graph_review_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES graph_review_sessions(id),
    action_json TEXT NOT NULL,
    applied INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_graph_review_actions_session
    ON graph_review_actions(session_id, created_at);
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

    async def create_graph_review_session(self, title: str = "Graph review") -> dict:
        assert self._db
        session_id = str(uuid.uuid4())
        await self._db.execute(
            "INSERT INTO graph_review_sessions (id, title) VALUES (?, ?)",
            (session_id, title),
        )
        await self._db.commit()
        return {"id": session_id, "title": title}

    async def list_graph_review_sessions(self, limit: int = 20) -> list[dict]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, title, started_at, last_active_at
               FROM graph_review_sessions
               WHERE EXISTS (
                   SELECT 1 FROM graph_review_messages m WHERE m.session_id = graph_review_sessions.id
               )
               ORDER BY last_active_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": row[0], "title": row[1], "started_at": row[2], "last_active_at": row[3]}
            for row in rows
        ]

    async def get_graph_review_session(self, session_id: str) -> dict | None:
        assert self._db
        cursor = await self._db.execute(
            "SELECT id, title, started_at, last_active_at FROM graph_review_sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        messages = await self.get_graph_review_history(session_id, limit=100)
        action_cursor = await self._db.execute(
            """SELECT action_json, applied, created_at FROM graph_review_actions
               WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        )
        actions = await action_cursor.fetchall()
        return {
            "id": row[0], "title": row[1], "started_at": row[2], "last_active_at": row[3],
            "messages": messages,
            "actions": [
                {"action": json.loads(action[0]), "applied": bool(action[1]), "created_at": action[2]}
                for action in actions
            ],
        }

    async def get_graph_review_history(self, session_id: str, limit: int = 10) -> list[dict]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT role, text FROM (
                   SELECT role, text, id FROM graph_review_messages
                   WHERE session_id = ? ORDER BY id DESC LIMIT ?
               ) ORDER BY id ASC""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row[0], "text": row[1]} for row in rows]

    async def save_graph_review_message(self, session_id: str, role: str, text: str) -> None:
        assert self._db
        if role == "user":
            count_cursor = await self._db.execute(
                "SELECT COUNT(*) FROM graph_review_messages WHERE session_id = ?", (session_id,)
            )
            (message_count,) = await count_cursor.fetchone()
            if message_count == 0:
                title = " ".join(text.split())[:80]
                await self._db.execute(
                    "UPDATE graph_review_sessions SET title = ? WHERE id = ?", (title, session_id)
                )
        await self._db.execute(
            "INSERT INTO graph_review_messages (session_id, role, text) VALUES (?, ?, ?)",
            (session_id, role, text),
        )
        await self._db.execute(
            "UPDATE graph_review_sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        await self._db.commit()

    async def save_graph_review_action(self, session_id: str, action: dict, applied: bool) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO graph_review_actions (session_id, action_json, applied) VALUES (?, ?, ?)",
            (session_id, json.dumps(action), int(applied)),
        )
        await self._db.commit()
