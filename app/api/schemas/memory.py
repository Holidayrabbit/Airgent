from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_session_id: str | None = None


class MemoryResponse(BaseModel):
    id: str
    content: str
    tags: list[str]
    source_session_id: str | None
    created_at: str
