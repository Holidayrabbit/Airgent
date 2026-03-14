from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, model_validator

from app.agents import prompts
from app.agents.context import AgentRunContext
from app.tools.registry import ToolRegistry

try:
    from agents import Agent, ModelSettings
except ImportError:  # pragma: no cover
    Agent = None  # type: ignore[assignment]
    ModelSettings = None  # type: ignore[assignment]

CONFIGS_DIR = Path(__file__).resolve().parent / "configs"


class AgentConfig(BaseModel):
    key: str
    version: str
    model: str
    max_turns: int
    instructions: str | None = None
    instructions_builder: str | None = None
    allow_high_risk_tools: bool = False
    tools: list[str]

    @model_validator(mode="after")
    def validate_instructions(self) -> "AgentConfig":
        instructions = self.instructions.strip() if self.instructions is not None else None
        instructions_builder = (
            self.instructions_builder.strip() if self.instructions_builder is not None else None
        )
        self.instructions = instructions or None
        self.instructions_builder = instructions_builder or None
        if bool(self.instructions) == bool(self.instructions_builder):
            raise ValueError("Exactly one of 'instructions' or 'instructions_builder' must be set.")
        return self


class RuntimeSpec(BaseModel):
    agent_key: str
    version: str
    model: str
    max_turns: int


class AgentRegistry:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    def _load_config(self, agent_key: str) -> AgentConfig:
        config_path = CONFIGS_DIR / f"{agent_key}.yaml"
        if not config_path.exists():
            raise KeyError(agent_key)
        return AgentConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))

    def list_configs(self) -> list[AgentConfig]:
        configs: list[AgentConfig] = []
        for config_path in sorted(CONFIGS_DIR.glob("*.yaml")):
            configs.append(AgentConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8"))))
        return configs

    def build(self, context: AgentRunContext) -> tuple[object, RuntimeSpec]:
        if Agent is None or ModelSettings is None:
            raise RuntimeError("openai-agents is not installed.")

        config = self._load_config(context.agent_key)
        instructions = prompts.resolve_instructions(
            context,
            instructions=config.instructions,
            instructions_builder=config.instructions_builder,
        )
        tools = self.tool_registry.resolve_enabled(
            config.tools,
            allow_high_risk_tools=config.allow_high_risk_tools,
        )
        agent = Agent(
            name=config.key,
            instructions=instructions,
            model=context.settings.default_model if config.model == "default" else config.model,
            model_settings=ModelSettings(temperature=0.2),
            tools=tools,
        )
        return (
            agent,
            RuntimeSpec(
                agent_key=config.key,
                version=config.version,
                model=config.model,
                max_turns=config.max_turns,
            ),
        )
