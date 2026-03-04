"""SQLite cache with FTS5 for message search.

Provides persistent storage for messages and full-text search.
Falls back to LIKE queries if FTS5 is not available.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from teams_tui.data.models import Message

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    sender_id TEXT,
    sender_name TEXT,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    is_edited INTEGER DEFAULT 0,
    message_type TEXT DEFAULT 'text'
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, sender_name,
    content='messages',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, sender_name)
    VALUES (new.rowid, new.content, new.sender_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, sender_name)
    VALUES ('delete', old.rowid, old.content, old.sender_name);
END;
"""


class Cache:
    """Async SQLite cache for messages with optional FTS5 search."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._has_fts = False

    async def initialize(self) -> None:
        """Open DB and create schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.executescript(_SCHEMA)

        # Try to create FTS5 virtual table
        try:
            await self._db.executescript(_FTS_SCHEMA)
            self._has_fts = True
            log.info("SQLite FTS5 available.")
        except Exception as e:
            log.info("FTS5 not available, using LIKE fallback: %s", e)
            self._has_fts = False

        await self._db.commit()

    async def store_messages(self, messages: list[Message]) -> None:
        """Insert or update messages in the cache."""
        if not self._db:
            return
        for msg in messages:
            await self._db.execute(
                """INSERT OR REPLACE INTO messages
                   (id, chat_id, sender_id, sender_name, content, timestamp, is_edited, message_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.id,
                    msg.chat_id,
                    msg.sender_id,
                    msg.sender_name,
                    msg.content,
                    msg.timestamp.isoformat(),
                    int(msg.is_edited),
                    msg.message_type,
                ),
            )
        await self._db.commit()

    async def get_messages(self, chat_id: str, limit: int = 100) -> list[dict]:
        """Retrieve cached messages for a chat."""
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT id, chat_id, sender_id, sender_name, content, timestamp,
                      is_edited, message_type
               FROM messages WHERE chat_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "chat_id": r[1], "sender_id": r[2],
                "sender_name": r[3], "content": r[4], "timestamp": r[5],
                "is_edited": bool(r[6]), "message_type": r[7],
            }
            for r in reversed(rows)
        ]

    async def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search messages by content. Uses FTS5 if available, LIKE otherwise."""
        if not self._db:
            return []

        if self._has_fts:
            cursor = await self._db.execute(
                """SELECT m.id, m.chat_id, m.sender_name, m.content, m.timestamp
                   FROM messages_fts f
                   JOIN messages m ON m.rowid = f.rowid
                   WHERE messages_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT id, chat_id, sender_name, content, timestamp
                   FROM messages
                   WHERE content LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (f"%{query}%", limit),
            )

        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "chat_id": r[1], "sender_name": r[2],
                "content": r[3], "timestamp": r[4],
            }
            for r in rows
        ]

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
