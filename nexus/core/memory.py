from __future__ import annotations

import json
from typing import Any

import aiosqlite

from .state import TaskState


class SessionMemory:
    """Durable session history, task state, and checkpoints backed by SQLite."""

    def __init__(self, db_path: str = "memory.db", session_id: str = "default"):
        self.db_path = db_path
        self.session_id = session_id

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    name TEXT,
                    tool_call_id TEXT,
                    tool_calls_json TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS task_state (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await self._migrate_messages(db)
            await db.commit()

    async def _migrate_messages(self, db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA table_info(messages)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        for name, sql_type in {
            "name": "TEXT",
            "tool_call_id": "TEXT",
            "tool_calls_json": "TEXT",
        }.items():
            if name not in columns:
                await db.execute(f"ALTER TABLE messages ADD COLUMN {name} {sql_type}")

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        *,
        name: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (
                    session_id, role, content, name, tool_call_id, tool_calls_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.session_id,
                    role,
                    content,
                    name,
                    tool_call_id,
                    json.dumps(tool_calls) if tool_calls else None,
                ),
            )
            await db.commit()

    async def get_history(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT role, content, name, tool_call_id, tool_calls_json
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (self.session_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        history: list[dict[str, Any]] = []
        for role, content, name, tool_call_id, tool_calls_json in rows:
            message: dict[str, Any] = {"role": role, "content": content}
            if name:
                message["name"] = name
            if tool_call_id:
                message["tool_call_id"] = tool_call_id
            if tool_calls_json:
                message["tool_calls"] = json.loads(tool_calls_json)
            history.append(message)
        return history

    async def save_state(self, state: TaskState) -> None:
        payload = state.model_dump_json()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO task_state (session_id, state_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (self.session_id, payload),
            )
            await db.commit()

    async def load_state(self) -> TaskState | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT state_json FROM task_state WHERE session_id = ?",
                (self.session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return TaskState.model_validate_json(row[0]) if row else None

    async def checkpoint(self, state: TaskState, reason: str) -> None:
        state.checkpoint_count += 1
        await self.save_state(state)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO checkpoints (session_id, state_json, reason) VALUES (?, ?, ?)",
                (self.session_id, state.model_dump_json(), reason),
            )
            await db.commit()

    async def latest_checkpoint(self) -> tuple[TaskState, str] | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT state_json, reason
                FROM checkpoints
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return TaskState.model_validate_json(row[0]), row[1]
