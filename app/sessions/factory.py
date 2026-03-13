from __future__ import annotations

from uuid import uuid4

from app.memory.store import LocalStore
from app.sessions.session import LocalConversationSession


class SessionFactory:
    def __init__(self, *, store: LocalStore, history_limit: int) -> None:
        self.store = store
        self.history_limit = history_limit

    def build_session_id(self, candidate: str | None) -> str:
        return candidate or uuid4().hex[:12]

    def create(self, session_id: str | None) -> LocalConversationSession:
        return LocalConversationSession(
            store=self.store,
            session_id=self.build_session_id(session_id),
            history_limit=self.history_limit,
        )
