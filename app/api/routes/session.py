from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas.session import (
    RewindPointResponse,
    RewindRequest,
    SessionDetailResponse,
    SessionSummaryResponse,
    TranscriptMessageResponse,
)
from app.core.errors import AppError

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionSummaryResponse])
async def list_sessions(request: Request) -> list[SessionSummaryResponse]:
    sessions = request.app.state.services.store.list_sessions(
        limit=request.app.state.services.settings.session_list_limit,
    )
    return [
        SessionSummaryResponse(
            session_id=session.session_id,
            title=session.title,
            agent_key=session.agent_key,
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_message=session.last_message,
        )
        for session in sessions
    ]


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(request: Request, session_id: str) -> SessionDetailResponse:
    messages = request.app.state.services.store.get_messages(session_id)
    if not messages:
        raise AppError(
            code="session_not_found",
            message=f"Session '{session_id}' was not found.",
            status_code=404,
            details={"session_id": session_id},
        )
    return SessionDetailResponse(
        session_id=session_id,
        messages=[
            TranscriptMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                agent_key=message.agent_key,
                created_at=message.created_at,
                metadata=message.metadata,
            )
            for message in messages
        ],
    )


@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict[str, str]:
    request.app.state.services.store.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@router.get("/{session_id}/rewind-points", response_model=list[RewindPointResponse])
async def list_rewind_points(request: Request, session_id: str) -> list[RewindPointResponse]:
    points = await request.app.state.services.runner.list_rewind_points(session_id)
    return [
        RewindPointResponse(entry_id=str(point.get("entryId") or ""), text=str(point.get("text") or ""))
        for point in points
    ]


@router.post("/{session_id}/rewind")
async def rewind_session(request: Request, session_id: str, payload: RewindRequest) -> dict[str, str]:
    if not payload.entry_id:
        raise AppError(
            code="invalid_rewind",
            message="entry_id is required.",
            status_code=400,
        )
    result = await request.app.state.services.runner.rewind(session_id, payload.entry_id)
    return {
        "status": "rewound",
        "session_id": session_id,
        "pi_session_id": result.get("pi_session_id", ""),
        "text": result.get("text", ""),
    }
