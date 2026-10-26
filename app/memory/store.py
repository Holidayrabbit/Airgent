from __future__ import annotations

import json
import re
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


def _days_since(iso_timestamp: str | None) -> float:
    if not iso_timestamp:
        return 0.0
    try:
        moment = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - moment).total_seconds() / 86400.0)


def _reciprocal_rank_fusion(
    *rankings: list[MemoryRecord], limit: int, k: int = 60
) -> list[MemoryRecord]:
    """Fuse multiple ranked lists (mem0/BM25-style hybrid retrieval).

    score(memory) = Σ 1 / (k + rank_in_list); ties keep first-seen order.
    """

    scores: dict[str, float] = {}
    order: dict[str, MemoryRecord] = {}
    for ranking in rankings:
        for position, record in enumerate(ranking):
            scores[record.id] = scores.get(record.id, 0.0) + 1.0 / (k + position + 1)
            order.setdefault(record.id, record)
    fused_ids = sorted(scores, key=lambda memory_id: scores[memory_id], reverse=True)[:limit]
    return [order[memory_id] for memory_id in fused_ids]


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
    kind: str = "fact"  # fact | preference | profile | project | event
    importance: float = 0.5
    access_count: int = 0
    last_accessed_at: str | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class MemoryBlock:
    key: str
    value: str
    updated_at: str


class LocalStore:
    def __init__(
        self,
        db_path: Path,
        *,
        dedup: bool = False,
        embedder: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.dedup = dedup
        self.embedder = embedder
        self._vec_available: bool | None = None
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        if self.embedder is not None:
            self._load_vec_extension(connection)
        return connection

    def _load_vec_extension(self, connection: sqlite3.Connection) -> None:
        """Load sqlite-vec once; unavailable extension degrades to keyword search."""

        if self._vec_available is not None:
            if self._vec_available:
                connection.enable_load_extension(True)
                import sqlite_vec

                connection.load_extension(sqlite_vec.loadable_path())
                connection.enable_load_extension(False)
            return
        try:
            import sqlite_vec

            connection.enable_load_extension(True)
            connection.load_extension(sqlite_vec.loadable_path())
            connection.enable_load_extension(False)
            self._vec_available = True
        except Exception:
            self._vec_available = False

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

                CREATE TABLE IF NOT EXISTS kernel_sessions (
                    airgent_session_id TEXT PRIMARY KEY,
                    pi_session_file TEXT NOT NULL,
                    model TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate(connection)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Idempotent column/table upgrades (memory system v2)."""

        memory_columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)")}
        additions = {
            "kind": "TEXT NOT NULL DEFAULT 'fact'",
            "importance": "REAL NOT NULL DEFAULT 0.5",
            "access_count": "INTEGER NOT NULL DEFAULT 0",
            "last_accessed_at": "TEXT",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in memory_columns:
                connection.execute(f"ALTER TABLE memories ADD COLUMN {column} {definition}")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_blocks (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                request_id TEXT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                dimension INTEGER NOT NULL,
                model TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_rowid INTEGER PRIMARY KEY,
                model TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("UPDATE memories SET updated_at = created_at WHERE updated_at = ''")

    def get_kernel_session(self, session_id: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT pi_session_file, model FROM kernel_sessions WHERE airgent_session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"pi_session_file": row["pi_session_file"], "model": row["model"]}

    def set_kernel_session(self, session_id: str, *, pi_session_file: str, model: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kernel_sessions (airgent_session_id, pi_session_file, model, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(airgent_session_id) DO UPDATE SET
                    pi_session_file = excluded.pi_session_file,
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (session_id, pi_session_file, model, utcnow()),
            )

    def delete_kernel_session(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM kernel_sessions WHERE airgent_session_id = ?", (session_id,)
            )

    # ------------------------------------------------------------------
    # Trace events (observability)
    # ------------------------------------------------------------------
    def append_trace_event(
        self,
        *,
        kind: str,
        session_id: str | None = None,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trace_events(session_id, request_id, kind, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    request_id,
                    kind,
                    json.dumps(payload or {}, ensure_ascii=True, default=str),
                    utcnow(),
                ),
            )

    def list_trace_events(self, limit: int = 100, *, session_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, session_id, request_id, kind, payload_json, created_at FROM trace_events"
        params: tuple[Any, ...]
        if session_id:
            query += " WHERE session_id = ?"
            params = (session_id,)
        query += " ORDER BY id DESC LIMIT ?"
        params = (*params, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "request_id": row["request_id"],
                "kind": row["kind"],
                "payload": _loads_json_dict(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def total_cost_usd(self) -> float:
        """Cumulative recorded spend, summed from per-turn usage metadata."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT metadata_json FROM transcripts WHERE role = 'system'"
            ).fetchall()
        total = 0.0
        for row in rows:
            metadata = _loads_json_dict(row["metadata_json"])
            if metadata.get("kind") != "usage":
                continue
            stats = metadata.get("stats")
            if isinstance(stats, dict):
                try:
                    total += float(stats.get("cost_usd") or 0.0)
                except (TypeError, ValueError):
                    continue
        return total

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
            connection.execute("DELETE FROM transcripts WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self.delete_kernel_session(session_id)

    def clear_transcript(self, session_id: str) -> None:
        """Drop the mirrored transcript rows (used when rebuilding after rewind)."""

        with self._connect() as connection:
            connection.execute("DELETE FROM transcripts WHERE session_id = ?", (session_id,))

    def add_memory(
        self,
        content: str,
        *,
        tags: list[str] | None = None,
        source_session_id: str | None = None,
        kind: str = "fact",
        importance: float = 0.5,
    ) -> MemoryRecord:
        normalized = content.strip()
        if self.dedup:
            existing = self._find_duplicate(normalized)
            if existing is not None:
                merged_tags = sorted(set(existing.tags) | {t.strip().lower() for t in tags or [] if t.strip()})
                self._touch_memory(existing.id)
                with self._connect() as connection:
                    connection.execute(
                        """
                        UPDATE memories
                        SET tags_json = ?, kind = ?, importance = MAX(importance, ?), updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(merged_tags, ensure_ascii=True),
                            kind,
                            importance,
                            utcnow(),
                            existing.id,
                        ),
                    )
                return self.get_memory(existing.id)  # type: ignore[return-value]

        record = MemoryRecord(
            id=uuid4().hex,
            content=normalized,
            tags=sorted({tag.strip().lower() for tag in tags or [] if tag.strip()}),
            source_session_id=source_session_id,
            created_at=utcnow(),
            kind=kind,
            importance=max(0.0, min(1.0, importance)),
            updated_at=utcnow(),
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories(id, content, tags_json, source_session_id, created_at,
                                     kind, importance, access_count, last_accessed_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    record.id,
                    record.content,
                    json.dumps(record.tags, ensure_ascii=True),
                    record.source_session_id,
                    record.created_at,
                    record.kind,
                    record.importance,
                    record.updated_at,
                ),
            )
            memory_rowid = cursor.lastrowid
        if memory_rowid is not None and self._vec_ready():
            # Best effort: a failed embedding must never lose the memory.
            try:
                with self._connect() as vec_connection:
                    self._ensure_vec_table(vec_connection, self.embedder.dimension or 0)  # type: ignore[attr-defined]
                    vector = self.embedder.embed_one(record.content)  # type: ignore[union-attr]
                    self._store_vector(vec_connection, memory_rowid, vector)
            except Exception:
                pass
        return record

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower()))

    def _find_duplicate(self, content: str) -> MemoryRecord | None:
        """Exact or near-duplicate detection (token Jaccard >= 0.8)."""

        candidates = self.list_memories(limit=500)
        target = self._tokens(content)
        if not target:
            return None
        for record in candidates:
            if record.content == content:
                return record
            other = self._tokens(record.content)
            if not other:
                continue
            jaccard = len(target & other) / len(target | other)
            if jaccard >= 0.8:
                return record
        return None

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._memory_from_row(row) if row else None

    def delete_memory(self, memory_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT rowid FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            connection.execute("DELETE FROM memory_embeddings WHERE memory_rowid = ?", (row["rowid"],))
            if self._vec_ready():
                try:
                    connection.execute("DELETE FROM memory_vec WHERE rowid = ?", (row["rowid"],))
                except Exception:
                    pass
        return True

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            content=row["content"],
            tags=_loads_json_list(row["tags_json"]),
            source_session_id=row["source_session_id"],
            created_at=row["created_at"],
            kind=row["kind"] if "kind" in row.keys() else "fact",
            importance=row["importance"] if "importance" in row.keys() else 0.5,
            access_count=row["access_count"] if "access_count" in row.keys() else 0,
            last_accessed_at=row["last_accessed_at"] if "last_accessed_at" in row.keys() else None,
            updated_at=row["updated_at"] if "updated_at" in row.keys() else "",
        )

    def list_memories(self, limit: int = 50, *, kind: str | None = None) -> list[MemoryRecord]:
        query = "SELECT * FROM memories"
        params: tuple[Any, ...] = ()
        if kind:
            query += " WHERE kind = ?"
            params = (kind,)
        query += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params = (*params, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def search_memories(
        self,
        query: str,
        *,
        limit: int = 5,
        decay_halflife_days: int = 0,
    ) -> list[MemoryRecord]:
        keyword_ranked = self._keyword_ranked(query, decay_halflife_days=decay_halflife_days)
        vector_ranked = self._vector_ranked(query) if self.embedder is not None else []
        if not vector_ranked:
            selected = keyword_ranked[:limit]
        else:
            selected = _reciprocal_rank_fusion(keyword_ranked, vector_ranked, limit=limit)
        if selected:
            self._touch_memory(*(record.id for record in selected))
        return selected

    # ------------------------------------------------------------------
    # Vector search (hybrid retrieval)
    # ------------------------------------------------------------------
    def _vec_ready(self) -> bool:
        return self.embedder is not None and self._vec_available is True

    def _ensure_vec_table(self, connection: sqlite3.Connection, dimension: int) -> None:
        meta = connection.execute("SELECT dimension FROM vector_meta WHERE id = 1").fetchone()
        if meta is not None and meta["dimension"] == dimension:
            return
        # (Re)create: different dimension means a different embedding model.
        connection.execute("DROP TABLE IF EXISTS memory_vec")
        connection.execute(
            f"CREATE VIRTUAL TABLE memory_vec USING vec0(embedding float[{dimension}])"
        )
        connection.execute("DELETE FROM vector_meta")
        connection.execute(
            "INSERT INTO vector_meta(id, dimension, model) VALUES(1, ?, ?)",
            (dimension, self.embedder.model if self.embedder else ""),
        )
        connection.execute("DELETE FROM memory_embeddings")
        connection.commit()

    @staticmethod
    def _serialize_vector(vector: list[float]) -> bytes:
        import struct

        return struct.pack(f"<{len(vector)}f", *vector)

    def _store_vector(self, connection: sqlite3.Connection, memory_rowid: int, vector: list[float]) -> None:
        connection.execute(
            "INSERT INTO memory_vec(rowid, embedding) VALUES(?, ?)",
            (memory_rowid, self._serialize_vector(vector)),
        )
        connection.execute(
            "INSERT INTO memory_embeddings(memory_rowid, model) VALUES(?, ?)",
            (memory_rowid, self.embedder.model if self.embedder else ""),
        )

    def embed_missing_memories(self, *, batch_size: int = 32) -> int:
        """Backfill embeddings for memories that lack one; returns count."""

        if not self._vec_ready():
            return 0
        with self._connect() as connection:
            self._ensure_vec_table(connection, self.embedder.dimension or 0)  # type: ignore[attr-defined]
            rows = connection.execute(
                """
                SELECT m.rowid AS rowid, m.content AS content
                FROM memories m
                LEFT JOIN memory_embeddings e ON e.memory_rowid = m.rowid
                WHERE e.memory_rowid IS NULL
                ORDER BY m.rowid ASC
                """
            ).fetchall()
        embedded = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            try:
                vectors = self.embedder.embed([row["content"] for row in batch])  # type: ignore[union-attr]
            except Exception:
                break
            with self._connect() as connection:
                for row, vector in zip(batch, vectors):
                    self._store_vector(connection, row["rowid"], vector)
                connection.commit()
            embedded += len(batch)
        return embedded

    def _vector_ranked(self, query: str, *, limit: int = 40) -> list[MemoryRecord]:
        """Nearest memories by cosine distance; empty on any failure."""

        if not self._vec_ready():
            return []
        try:
            query_vector = self.embedder.embed_one(query)  # type: ignore[union-attr]
        except Exception:
            return []
        with self._connect() as connection:
            meta = connection.execute("SELECT dimension FROM vector_meta WHERE id = 1").fetchone()
            if meta is None or len(query_vector) != meta["dimension"]:  # type: ignore[index]
                return []
            try:
                rows = connection.execute(
                    """
                    SELECT v.rowid, distance
                    FROM memory_vec v
                    WHERE embedding MATCH ?
                      AND k = ?
                    ORDER BY distance
                    """,
                    (self._serialize_vector(query_vector), limit),
                ).fetchall()
            except Exception:
                return []
            ranked: list[MemoryRecord] = []
            for row in rows:
                record_row = connection.execute(
                    "SELECT * FROM memories WHERE rowid = ?", (row["rowid"],)
                ).fetchone()
                if record_row is not None:
                    ranked.append(self._memory_from_row(record_row))
        return ranked

    def _keyword_ranked(
        self, query: str, *, decay_halflife_days: int = 0, limit: int = 40
    ) -> list[MemoryRecord]:
        """The pre-existing keyword ranking, unbounded by the final limit."""

        tokens = [token for token in {part.lower() for part in query.split()} if token]
        memories = self.list_memories(limit=500)
        if not tokens:
            return memories

        def relevance(record: MemoryRecord) -> float:
            haystack = f"{record.content.lower()} {' '.join(record.tags)}"
            hits = sum(1 for token in tokens if token in haystack)
            if hits == 0:
                return 0.0
            weight = 0.5 + record.importance
            if decay_halflife_days > 0:
                anchor = record.last_accessed_at or record.updated_at or record.created_at
                days = _days_since(anchor)
                weight *= 0.5 ** (days / decay_halflife_days)
            return hits * weight

        ranked = [record for record in memories if relevance(record) > 0]
        ranked.sort(key=relevance, reverse=True)
        return ranked[:limit]

    def _touch_memory(self, *memory_ids: str) -> None:
        """Record retrieval access (feeds the decay clock)."""

        now = utcnow()
        with self._connect() as connection:
            connection.executemany(
                """
                UPDATE memories
                SET access_count = access_count + 1, last_accessed_at = ?
                WHERE id = ?
                """,
                [(now, memory_id) for memory_id in memory_ids],
            )

    # ------------------------------------------------------------------
    # Core memory blocks (Letta-style always-in-context blocks)
    # ------------------------------------------------------------------
    def get_block(self, key: str) -> MemoryBlock | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT key, value, updated_at FROM memory_blocks WHERE key = ?", (key,)
            ).fetchone()
        return MemoryBlock(key=row["key"], value=row["value"], updated_at=row["updated_at"]) if row else None

    def set_block(self, key: str, value: str) -> MemoryBlock:
        block = MemoryBlock(key=key.strip(), value=value.strip(), updated_at=utcnow())
        if not block.key:
            raise ValueError("memory block key must not be empty")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_blocks(key, value, updated_at) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (block.key, block.value, block.updated_at),
            )
        return block

    def list_blocks(self) -> list[MemoryBlock]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value, updated_at FROM memory_blocks ORDER BY key ASC"
            ).fetchall()
        return [MemoryBlock(key=row["key"], value=row["value"], updated_at=row["updated_at"]) for row in rows]

    def delete_block(self, key: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM memory_blocks WHERE key = ?", (key,))
        return cursor.rowcount > 0

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
