from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas.session import (
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
            )
            for message in messages
        ],
    )


@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict[str, str]:
    request.app.state.services.store.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}
