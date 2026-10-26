from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.schemas.memory import (
    MemoryBlockResponse,
    MemoryBlockUpsertRequest,
    MemoryCreateRequest,
    MemoryResponse,
)
from app.memory.store import MemoryRecord

router = APIRouter(prefix="/memories", tags=["memory"])


def _response(record: MemoryRecord) -> MemoryResponse:
    return MemoryResponse(
        id=record.id,
        content=record.content,
        tags=record.tags,
        source_session_id=record.source_session_id,
        created_at=record.created_at,
        kind=record.kind,
        importance=record.importance,
    )


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    kind: str | None = Query(default=None),
) -> list[MemoryResponse]:
    records = request.app.state.services.store.list_memories(limit=limit, kind=kind)
    return [_response(record) for record in records]


@router.get("/search", response_model=list[MemoryResponse])
async def search_memories(
    request: Request,
    q: str = Query(default="", alias="query"),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[MemoryResponse]:
    records = request.app.state.services.store.search_memories(q, limit=limit)
    return [_response(record) for record in records]


@router.post("", response_model=MemoryResponse)
async def create_memory(request: Request, payload: MemoryCreateRequest) -> MemoryResponse:
    record = request.app.state.services.store.add_memory(
        payload.content,
        tags=payload.tags,
        source_session_id=payload.source_session_id,
        kind=payload.kind,
        importance=payload.importance,
    )
    return _response(record)


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, bool]:
    deleted = request.app.state.services.store.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": True}


@router.get("/blocks", response_model=list[MemoryBlockResponse])
async def list_blocks(request: Request) -> list[MemoryBlockResponse]:
    return [
        MemoryBlockResponse(key=block.key, value=block.value, updated_at=block.updated_at)
        for block in request.app.state.services.store.list_blocks()
    ]


@router.put("/blocks", response_model=MemoryBlockResponse)
async def upsert_block(request: Request, payload: MemoryBlockUpsertRequest) -> MemoryBlockResponse:
    block = request.app.state.services.store.set_block(payload.key, payload.value)
    return MemoryBlockResponse(key=block.key, value=block.value, updated_at=block.updated_at)


@router.delete("/blocks/{key}")
async def delete_block(request: Request, key: str) -> dict[str, bool]:
    deleted = request.app.state.services.store.delete_block(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="block not found")
    return {"deleted": True}
