from __future__ import annotations

import pytest

from app.core.config import Settings


def test_all_optional_features_default_off(monkeypatch) -> None:
    for name in (
        "AIRGENT_SANDBOX",
        "AIRGENT_SANDBOX_PROVIDER",
        "AIRGENT_SKILLS_REGISTRY",
        "AIRGENT_MCP",
        "AIRGENT_SUBAGENTS",
        "AIRGENT_GUARDRAILS",
        "AIRGENT_HOOKS",
        "AIRGENT_MEMORY_AUTO_EXTRACT",
        "AIRGENT_MEMORY_DECAY_DAYS",
        "AIRGENT_MEMORY_CORE_BLOCKS",
        "AIRGENT_MEMORY_DEDUP",
        "AIRGENT_TRACING",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)
    features = settings.enabled_features()

    assert features["sandbox"] == "none"
    assert features["skills_registry"] is False
    assert features["mcp"] is False
    assert features["subagents"] is False
    assert features["guardrails"] is False
    assert features["hooks"] is False
    assert features["memory_auto_extract"] is False
    assert features["memory_decay"] is False
    assert features["memory_core_blocks"] is False
    assert features["memory_dedup"] is False
    assert features["tracing"] is False


def test_features_enable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("AIRGENT_SANDBOX", "seatbelt")
    monkeypatch.setenv("AIRGENT_MCP", "true")
    monkeypatch.setenv("AIRGENT_MEMORY_DECAY_DAYS", "30")
    monkeypatch.setenv("AIRGENT_MEMORY_DEDUP", "1")

    settings = Settings(_env_file=None)
    features = settings.enabled_features()

    assert features["sandbox"] == "seatbelt"
    assert features["mcp"] is True
    assert features["memory_decay"] is True
    assert features["memory_dedup"] is True


def test_feature_config_paths_default_under_data_dir(tmp_path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.mcp_config == tmp_path / "mcp.json"
    assert settings.guardrails_config == tmp_path / "guardrails.json"
    assert settings.hooks_config == tmp_path / "hooks.json"
    assert settings.skills_registry_file == tmp_path / "skills.json"


@pytest.mark.parametrize("provider", ["none", "off", ""])
def test_sandbox_provider_none_aliases(provider: str, tmp_path) -> None:
    from app.sandbox.factory import create_sandbox

    spec = create_sandbox(Settings(_env_file=None, sandbox_provider=provider, data_dir=tmp_path))
    assert spec.provider == "none"
