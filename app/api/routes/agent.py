from __future__ import annotations

import json
from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.schemas.agent import (
    AgentOptionResponse,
    AgentRunRequest,
    AgentRunResponse,
    PermissionRespondRequest,
)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/permission")
async def respond_permission(request: Request, payload: PermissionRespondRequest) -> dict[str, bool]:
    """Answer a permission_request emitted during an /agent/stream run."""

    answered = await request.app.state.services.runner.respond_permission(
        payload.session_id, payload.request_id, allowed=payload.allowed
    )
    return {"answered": answered}


@router.get("/available", response_model=list[AgentOptionResponse])
async def list_agents(request: Request) -> list[AgentOptionResponse]:
    settings = request.app.state.services.settings
    return [
        AgentOptionResponse(
            key=settings.default_agent_key,
            model=settings.default_model,
            version="pi-kernel",
            is_default=True,
        )
    ]


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(request: Request, payload: AgentRunRequest) -> AgentRunResponse:
    result = await request.app.state.services.runner.run(payload, request_id=uuid4().hex)
    return AgentRunResponse(
        request_id=result.request_id,
        session_id=result.session_id,
        agent_key=result.agent_key,
        output=result.output,
        context=result.context,
    )


@router.post("/stream")
async def stream_agent(request: Request, payload: AgentRunRequest) -> StreamingResponse:
    async def event_stream():
        try:
            async for event in request.app.state.services.runner.stream(payload, request_id=uuid4().hex):
                yield json.dumps(asdict(event), ensure_ascii=True) + "\n"
        except Exception as exc:
            yield json.dumps(
                {
                    "kind": "error",
                    "summary": str(exc) or exc.__class__.__name__,
                    "detail": "",
                    "session_id": None,
                    "output": None,
                },
                ensure_ascii=True,
            ) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
