from __future__ import annotations

from dataclasses import dataclass

from app.memory.store import LocalStore, MemoryRecord, TranscriptMessage


@dataclass(frozen=True)
class ContextSnapshot:
    recent_messages: list[TranscriptMessage]
    memories: list[MemoryRecord]


class ContextBuilder:
    def __init__(
        self,
        *,
        store: LocalStore,
        transcript_limit: int,
        memory_limit: int,
    ) -> None:
        self.store = store
        self.transcript_limit = transcript_limit
        self.memory_limit = memory_limit

    def build(self, *, session_id: str, query: str) -> ContextSnapshot:
        return ContextSnapshot(
            recent_messages=self.store.get_messages(session_id, limit=self.transcript_limit),
            memories=self.store.search_memories(query, limit=self.memory_limit),
        )
