from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.kernel.setup import prepare_environment


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        openai_api_key="sk-test",
        openai_base_url=None,
        default_model="anthropic/claude-sonnet-4.5",
        data_dir=tmp_path / "data",
        pi_agent_dir=tmp_path / "pi" / "agent",
        pi_session_dir=tmp_path / "pi" / "sessions",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_default_setup_deploys_only_memory_extension(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    environment = prepare_environment(settings)

    assert environment.sandbox.provider == "none"
    assert environment.extensions == ("airgent-memory.ts",)
    extensions_dir = settings.pi_agent_dir / "extensions"
    assert {path.name for path in extensions_dir.glob("*.ts")} == {"airgent-memory.ts"}
    assert "spawn_subagent" not in (settings.pi_agent_dir / "APPEND_SYSTEM.md").read_text()


def test_subagent_feature_deploys_extension_and_addendum(tmp_path: Path) -> None:
    settings = _settings(tmp_path, subagents_enabled=True, subagents_max_depth=2)

    environment = prepare_environment(settings)

    assert "airgent-subagent.ts" in environment.extensions
    rendered = (settings.pi_agent_dir / "extensions" / "airgent-subagent.ts").read_text()
    assert "__AIRGENT_PI_BIN__" not in rendered
    assert "anthropic/claude-sonnet-4.5" in rendered
    assert json.dumps(2) in rendered
    assert "spawn_subagent" in (settings.pi_agent_dir / "APPEND_SYSTEM.md").read_text()


def test_guardrails_feature_writes_default_policy(tmp_path: Path) -> None:
    settings = _settings(tmp_path, guardrails_enabled=True)

    environment = prepare_environment(settings)

    assert "airgent-guardrails.ts" in environment.extensions
    rendered = (settings.pi_agent_dir / "extensions" / "airgent-guardrails.ts").read_text()
    assert "__AIRGENT_GUARDRAILS_CONFIG__" not in rendered
    policy = json.loads(settings.guardrails_config.read_text())
    assert policy["denied_commands"]


def test_mcp_feature_deploys_bridge_and_config(tmp_path: Path) -> None:
    settings = _settings(tmp_path, mcp_enabled=True)

    environment = prepare_environment(settings)

    assert "airgent-mcp.ts" in environment.extensions
    rendered = (settings.pi_agent_dir / "extensions" / "airgent-mcp.ts").read_text()
    assert "__AIRGENT_MCP_CONFIG__" not in rendered
    assert json.loads(settings.mcp_config.read_text()) == {"mcpServers": {}}


def test_disabling_features_removes_stale_extensions(tmp_path: Path) -> None:
    settings = _settings(tmp_path, subagents_enabled=True)
    prepare_environment(settings)
    assert (settings.pi_agent_dir / "extensions" / "airgent-subagent.ts").exists()

    prepare_environment(_settings(tmp_path))

    assert not (settings.pi_agent_dir / "extensions" / "airgent-subagent.ts").exists()


def test_skills_overlay_written_when_registry_filters(tmp_path: Path) -> None:
    skills_root = tmp_path / "project" / ".agents" / "skills"
    for name in ("alpha", "beta"):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Demo skill.\n---\n", encoding="utf-8"
        )
    settings = _settings(
        tmp_path,
        project_root=tmp_path / "project",
        skills_root=skills_root,
        skills_user_root=tmp_path / "user-skills",
        skills_registry_enabled=True,
    )

    from app.skills.registry import SkillRegistry

    registry = SkillRegistry(settings)
    assert registry.set_enabled("beta", False)
    prepare_environment(settings)

    overlay = json.loads((settings.pi_agent_dir / "settings.json").read_text())
    assert overlay["skills"] == [str(skills_root / "alpha")]


def test_skills_overlay_absent_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    prepare_environment(settings)
    assert not (settings.pi_agent_dir / "settings.json").exists()
