from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=request.app.state.services.settings.app_name,
    )
