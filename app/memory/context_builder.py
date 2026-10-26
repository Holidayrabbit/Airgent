from __future__ import annotations

from dataclasses import dataclass, field

from app.memory.store import LocalStore, MemoryBlock, MemoryRecord


@dataclass(frozen=True)
class ContextSnapshot:
    memories: list[MemoryRecord]
    blocks: list[MemoryBlock] = field(default_factory=list)


class ContextBuilder:
    def __init__(
        self,
        *,
        store: LocalStore,
        memory_limit: int,
        decay_halflife_days: int = 0,
        core_blocks: bool = False,
    ) -> None:
        self.store = store
        self.memory_limit = memory_limit
        self.decay_halflife_days = decay_halflife_days
        self.core_blocks = core_blocks

    def build(self, *, session_id: str, query: str) -> ContextSnapshot:
        return ContextSnapshot(
            memories=self.store.search_memories(
                query, limit=self.memory_limit, decay_halflife_days=self.decay_halflife_days
            ),
            blocks=self.store.list_blocks() if self.core_blocks else [],
        )
