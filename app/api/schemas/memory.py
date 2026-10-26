from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_session_id: str | None = None
    kind: str = Field(default="fact", max_length=32)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryResponse(BaseModel):
    id: str
    content: str
    tags: list[str]
    source_session_id: str | None
    created_at: str
    kind: str = "fact"
    importance: float = 0.5


class MemoryBlockResponse(BaseModel):
    key: str
    value: str
    updated_at: str


class MemoryBlockUpsertRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1)
