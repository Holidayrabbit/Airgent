from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/features", tags=["features"])


class FeaturesResponse(BaseModel):
    kernel: str
    model: str
    features: dict[str, str | bool]


@router.get("", response_model=FeaturesResponse)
async def get_features(request: Request) -> FeaturesResponse:
    settings = request.app.state.services.settings
    return FeaturesResponse(
        kernel=settings.pi_bin,
        model=settings.default_model,
        features=settings.enabled_features(),
    )
