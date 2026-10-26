from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1)
    session_id: str | None = Field(default=None, max_length=128)
    agent_key: str = Field(default="root_assistant", max_length=128)
    plan: bool = Field(
        default=False,
        description="Plan mode: read-only run that ends with an implementation proposal.",
    )


class AgentRunResponse(BaseModel):
    request_id: str
    session_id: str
    agent_key: str
    output: str
    context: dict[str, Any]


class AgentOptionResponse(BaseModel):
    key: str
    model: str
    version: str
    is_default: bool


class PermissionRespondRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    allowed: bool
