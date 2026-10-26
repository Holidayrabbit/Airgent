from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.skills.registry import SkillRegistry

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillResponse(BaseModel):
    name: str
    description: str
    source: str
    path: str
    enabled: bool
    registry_enabled: bool


@router.get("", response_model=list[SkillResponse])
async def list_skills(request: Request) -> list[SkillResponse]:
    settings = request.app.state.services.settings
    registry = SkillRegistry(settings)
    return [
        SkillResponse(
            name=entry.name,
            description=entry.description,
            source=entry.source,
            path=str(entry.path),
            enabled=registry.is_enabled(entry.name),
            registry_enabled=settings.skills_registry_enabled,
        )
        for entry in registry.discover()
    ]


@router.post("/{name}/enable", response_model=SkillResponse)
async def enable_skill(request: Request, name: str) -> SkillResponse:
    return await _set_skill(request, name, True)


@router.post("/{name}/disable", response_model=SkillResponse)
async def disable_skill(request: Request, name: str) -> SkillResponse:
    return await _set_skill(request, name, False)


async def _set_skill(request: Request, name: str, enabled: bool) -> SkillResponse:
    settings = request.app.state.services.settings
    registry = SkillRegistry(settings)
    if not registry.set_enabled(name, enabled):
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
    for entry in registry.discover():
        if entry.name == name:
            return SkillResponse(
                name=entry.name,
                description=entry.description,
                source=entry.source,
                path=str(entry.path),
                enabled=registry.is_enabled(entry.name),
                registry_enabled=settings.skills_registry_enabled,
            )
    raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
