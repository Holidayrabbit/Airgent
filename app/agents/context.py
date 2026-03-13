from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import Settings
from app.memory.context_builder import ContextSnapshot
from app.memory.store import LocalStore


@dataclass
class AgentRunContext:
    settings: Settings
    store: LocalStore
    request_id: str
    agent_key: str
    session_id: str
    user_input: str
    context_snapshot: ContextSnapshot
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("airgent.agent"))
