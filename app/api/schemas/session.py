from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SessionSummaryResponse(BaseModel):
    session_id: str
    title: str
    agent_key: str
    created_at: str
    updated_at: str
    last_message: str | None


class TranscriptMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    agent_key: str
    created_at: str
    metadata: dict[str, Any]


class SessionDetailResponse(BaseModel):
    session_id: str
    messages: list[TranscriptMessageResponse]
