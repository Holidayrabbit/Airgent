from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Request

from app.api.schemas.agent import AgentRunRequest, AgentRunResponse

router = APIRouter(prefix="/agent", tags=["agent"])


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
