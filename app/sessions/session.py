from __future__ import annotations

import asyncio
from typing import Any

from app.memory.store import LocalStore


class LocalConversationSession:
    session_id: str
    session_settings = None

    def __init__(self, *, store: LocalStore, session_id: str, history_limit: int | None = None) -> None:
        self.store = store
        self.session_id = session_id
        self.history_limit = history_limit

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        resolved_limit = limit if limit is not None else self.history_limit
        return await asyncio.to_thread(self.store.get_session_items, self.session_id, resolved_limit)

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.store.append_session_items, self.session_id, items)

    async def pop_item(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.pop_session_item, self.session_id)

    async def clear_session(self) -> None:
        await asyncio.to_thread(self.store.clear_session_items, self.session_id)
