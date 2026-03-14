from __future__ import annotations

from types import SimpleNamespace

from app.agents.registry import AgentRegistry


def test_list_configs_includes_root_assistant() -> None:
    registry = AgentRegistry(tool_registry=SimpleNamespace())

    configs = registry.list_configs()

    assert configs
    assert any(config.key == "root_assistant" for config in configs)
