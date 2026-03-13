from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.schemas.memory import MemoryCreateRequest, MemoryResponse

router = APIRouter(prefix="/memories", tags=["memory"])


@router.get("", response_model=list[MemoryResponse])
async def list_memories(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> list[MemoryResponse]:
    records = request.app.state.services.store.list_memories(limit=limit)
    return [
        MemoryResponse(
            id=record.id,
            content=record.content,
            tags=record.tags,
            source_session_id=record.source_session_id,
            created_at=record.created_at,
        )
        for record in records
    ]


@router.get("/search", response_model=list[MemoryResponse])
async def search_memories(
    request: Request,
    q: str = Query(default="", alias="query"),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[MemoryResponse]:
    records = request.app.state.services.store.search_memories(q, limit=limit)
    return [
        MemoryResponse(
            id=record.id,
            content=record.content,
            tags=record.tags,
            source_session_id=record.source_session_id,
            created_at=record.created_at,
        )
        for record in records
    ]


@router.post("", response_model=MemoryResponse)
async def create_memory(request: Request, payload: MemoryCreateRequest) -> MemoryResponse:
    record = request.app.state.services.store.add_memory(
        payload.content,
        tags=payload.tags,
        source_session_id=payload.source_session_id,
    )
    return MemoryResponse(
        id=record.id,
        content=record.content,
        tags=record.tags,
        source_session_id=record.source_session_id,
        created_at=record.created_at,
    )
