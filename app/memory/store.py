from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _truncate_title(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:48] + ("..." if len(compact) > 48 else "")


def _loads_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    value = json.loads(raw)
    return value if isinstance(value, list) else []


def _loads_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    agent_key: str
    created_at: str
    updated_at: str
    last_message: str | None


@dataclass(frozen=True)
class TranscriptMessage:
    id: int
    session_id: str
    role: str
    content: str
    agent_key: str
    created_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    tags: list[str]
    source_session_id: str | None
    created_at: str


class LocalStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS session_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_session_items_session_id
                ON session_items(session_id, id);

                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_transcripts_session_id
                ON transcripts(session_id, id);

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source_session_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memories_created_at
                ON memories(created_at DESC);

                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    input TEXT NOT NULL,
                    schedule_kind TEXT NOT NULL,
                    schedule_value TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    one_shot INTEGER NOT NULL DEFAULT 0,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
                ON scheduled_jobs(next_run_at) WHERE enabled = 1;
                """
            )

    def upsert_session(self, session_id: str, agent_key: str, *, title: str | None = None) -> None:
        now = utcnow()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT title FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            resolved_title = title or (current["title"] if current else "New chat")
            connection.execute(
                """
                INSERT INTO sessions(session_id, title, agent_key, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    title = excluded.title,
                    agent_key = excluded.agent_key,
                    updated_at = excluded.updated_at
                """,
                (session_id, resolved_title, agent_key, now, now),
            )

    def append_session_items(self, session_id: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        now = utcnow()
        payloads = [
            (
                session_id,
                json.dumps(item, ensure_ascii=True, default=str),
                now,
            )
            for item in items
        ]
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO session_items(session_id, item_json, created_at) VALUES(?, ?, ?)",
                payloads,
            )

    def get_session_items(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT item_json FROM session_items WHERE session_id = ? ORDER BY id ASC"
        params: tuple[Any, ...]
        if limit is None:
            params = (session_id,)
        else:
            query = (
                "SELECT item_json FROM ("
                "SELECT item_json, id FROM session_items WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC"
            )
            params = (session_id, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [json.loads(row["item_json"]) for row in rows]

    def pop_session_item(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, item_json FROM session_items WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM session_items WHERE id = ?", (row["id"],))
        return json.loads(row["item_json"])

    def clear_session_items(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM session_items WHERE session_id = ?", (session_id,))

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        agent_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utcnow()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT title, created_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            title = current["title"] if current else "New chat"
            if (not current or title == "New chat") and role == "user":
                title = _truncate_title(content)
            connection.execute(
                """
                INSERT INTO sessions(session_id, title, agent_key, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    title = excluded.title,
                    agent_key = excluded.agent_key,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    title,
                    agent_key,
                    current["created_at"] if current else now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO transcripts(session_id, role, content, agent_key, metadata_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    agent_key,
                    json.dumps(metadata or {}, ensure_ascii=True, default=str),
                    now,
                ),
            )

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    s.session_id,
                    s.title,
                    s.agent_key,
                    s.created_at,
                    s.updated_at,
                    (
                        SELECT t.content
                        FROM transcripts t
                        WHERE t.session_id = s.session_id
                        ORDER BY t.id DESC
                        LIMIT 1
                    ) AS last_message
                FROM sessions s
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SessionSummary(
                session_id=row["session_id"],
                title=row["title"],
                agent_key=row["agent_key"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                last_message=row["last_message"],
            )
            for row in rows
        ]

    def get_messages(self, session_id: str, limit: int | None = None) -> list[TranscriptMessage]:
        query = """
            SELECT id, session_id, role, content, agent_key, metadata_json, created_at
            FROM transcripts
            WHERE session_id = ?
            ORDER BY id ASC
        """
        params: tuple[Any, ...]
        if limit is None:
            params = (session_id,)
        else:
            query = """
                SELECT id, session_id, role, content, agent_key, metadata_json, created_at
                FROM (
                    SELECT id, session_id, role, content, agent_key, metadata_json, created_at
                    FROM transcripts
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
            """
            params = (session_id, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            TranscriptMessage(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                agent_key=row["agent_key"],
                created_at=row["created_at"],
                metadata=_loads_json_dict(row["metadata_json"]),
            )
            for row in rows
        ]

    def delete_session(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM session_items WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM transcripts WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def add_memory(
        self,
        content: str,
        *,
        tags: list[str] | None = None,
        source_session_id: str | None = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=uuid4().hex,
            content=content.strip(),
            tags=sorted({tag.strip().lower() for tag in tags or [] if tag.strip()}),
            source_session_id=source_session_id,
            created_at=utcnow(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories(id, content, tags_json, source_session_id, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.content,
                    json.dumps(record.tags, ensure_ascii=True),
                    record.source_session_id,
                    record.created_at,
                ),
            )
        return record

    def list_memories(self, limit: int = 50) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, content, tags_json, source_session_id, created_at
                FROM memories
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            MemoryRecord(
                id=row["id"],
                content=row["content"],
                tags=_loads_json_list(row["tags_json"]),
                source_session_id=row["source_session_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def search_memories(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        tokens = [token for token in {part.lower() for part in query.split()} if token]
        memories = self.list_memories(limit=200)
        if not tokens:
            return memories[:limit]

        def score(record: MemoryRecord) -> tuple[int, str]:
            haystack = f"{record.content.lower()} {' '.join(record.tags)}"
            return (sum(1 for token in tokens if token in haystack), record.created_at)

        ranked = [record for record in memories if score(record)[0] > 0]
        ranked.sort(key=score, reverse=True)
        return ranked[:limit]

    # ------------------------------------------------------------------
    # Scheduled Jobs (Cron)
    # ------------------------------------------------------------------
    def initialize_cron(self) -> None:
        """Idempotent — called every startup to ensure table exists."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    input TEXT NOT NULL,
                    schedule_kind TEXT NOT NULL,
                    schedule_value TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    one_shot INTEGER NOT NULL DEFAULT 0,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
                ON scheduled_jobs(next_run_at) WHERE enabled = 1;
                """
            )

    def insert_cron_job(
        self,
        *,
        id: str,
        name: str,
        agent_key: str,
        input: str,
        schedule_kind: str,
        schedule_value: str,
        enabled: bool,
        one_shot: bool,
        last_run_at: str | None,
        next_run_at: str | None,
        created_at: str,
        metadata_json: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_jobs(
                    id, name, agent_key, input, schedule_kind, schedule_value,
                    enabled, one_shot, last_run_at, next_run_at, created_at, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id, name, agent_key, input, schedule_kind, schedule_value,
                    int(enabled), int(one_shot), last_run_at, next_run_at, created_at, metadata_json,
                ),
            )

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_cron_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scheduled_jobs ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_due_cron_jobs(self, now_iso: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                """,
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_cron_job(self, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        setters = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE scheduled_jobs SET {setters} WHERE id = ?",
                values,
            )
            row = connection.execute(
                "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_cron_job(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM scheduled_jobs WHERE id = ?", (job_id,)
            )
        return cursor.rowcount > 0
