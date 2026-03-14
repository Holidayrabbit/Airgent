from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.agents.registry as registry_module
from app.agents.context import AgentRunContext
from app.agents.registry import AgentRegistry
from app.core.config import Settings
from app.memory.context_builder import ContextSnapshot
from app.memory.store import MemoryRecord


class FakeAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class FakeModelSettings:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def _build_context(tmp_path: Path, agent_key: str) -> AgentRunContext:
    return AgentRunContext(
        settings=Settings(
            project_root=tmp_path,
            data_dir=tmp_path / ".airgent",
            db_path=tmp_path / ".airgent" / "airgent.db",
            skills_root=tmp_path / ".agents" / "skills",
        ),
        store=SimpleNamespace(),
        request_id="req-1",
        agent_key=agent_key,
        session_id="sess-1",
        user_input="hello",
        context_snapshot=ContextSnapshot(
            recent_messages=[],
            memories=[
                MemoryRecord(
                    id="mem-1",
                    content="The user prefers terse engineering answers.",
                    tags=["style"],
                    source_session_id=None,
                    created_at="2026-03-14T00:00:00+00:00",
                )
            ],
        ),
    )


def test_list_configs_includes_root_assistant() -> None:
    registry = AgentRegistry(tool_registry=SimpleNamespace())

    configs = registry.list_configs()

    assert configs
    root = next(config for config in configs if config.key == "root_assistant")
    assert root.instructions is not None
    assert root.instructions_builder is None


def test_build_uses_inline_instructions(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "writer.yaml").write_text(
        """
key: writer
version: v1
model: default
max_turns: 6
instructions: |
  You are a writing-focused agent.
allow_high_risk_tools: false
tools:
  - read_file
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module, "CONFIGS_DIR", config_dir)
    monkeypatch.setattr(registry_module, "Agent", FakeAgent)
    monkeypatch.setattr(registry_module, "ModelSettings", FakeModelSettings)
    registry = AgentRegistry(
        tool_registry=SimpleNamespace(resolve_enabled=lambda keys, allow_high_risk_tools: list(keys))
    )

    agent, runtime_spec = registry.build(_build_context(tmp_path, "writer"))

    assert agent.kwargs["name"] == "writer"
    assert "You are a writing-focused agent." in agent.kwargs["instructions"]
    assert "Runtime context:" in agent.kwargs["instructions"]
    assert "The user prefers terse engineering answers." in agent.kwargs["instructions"]
    assert runtime_spec.agent_key == "writer"
    assert runtime_spec.model == "default"


def test_build_supports_legacy_instructions_builder(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "legacy.yaml").write_text(
        """
key: legacy
version: v1
model: gpt-4o
max_turns: 4
instructions_builder: build_root_instructions
tools:
  - read_file
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module, "CONFIGS_DIR", config_dir)
    monkeypatch.setattr(registry_module, "Agent", FakeAgent)
    monkeypatch.setattr(registry_module, "ModelSettings", FakeModelSettings)
    registry = AgentRegistry(
        tool_registry=SimpleNamespace(resolve_enabled=lambda keys, allow_high_risk_tools: list(keys))
    )

    agent, _ = registry.build(_build_context(tmp_path, "legacy"))

    assert "You are Airgent" in agent.kwargs["instructions"]
    assert "Runtime context:" in agent.kwargs["instructions"]
