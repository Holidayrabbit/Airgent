from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CronJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    agent_key: str = Field(default="root_assistant", max_length=128)
    input: str = Field(min_length=1, max_length=4096)
    schedule_kind: str = Field(pattern="^(cron|once|interval)$")
    schedule_value: str = Field(min_length=1, max_length=64)  # cron expr, "once", or seconds
    enabled: bool = Field(default=True)
    one_shot: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CronJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    agent_key: str | None = Field(default=None, max_length=128)
    input: str | None = Field(default=None, min_length=1, max_length=4096)
    schedule_kind: str | None = Field(default=None, pattern="^(cron|once|interval)$")
    schedule_value: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    one_shot: bool | None = None
    metadata: dict[str, Any] | None = None


class CronJobResponse(BaseModel):
    id: str
    name: str
    agent_key: str
    input: str
    schedule_kind: str
    schedule_value: str
    enabled: bool
    one_shot: bool
    last_run_at: str | None
    next_run_at: str | None
    created_at: str
    metadata: dict[str, Any]
