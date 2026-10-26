from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.sandbox.base import SandboxError
from app.sandbox.factory import create_sandbox
from app.sandbox.seatbelt import render_seatbelt_profile


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        data_dir=tmp_path / "data",
        pi_agent_dir=tmp_path / "pi" / "agent",
        pi_session_dir=tmp_path / "pi" / "sessions",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_default_sandbox_is_none(tmp_path: Path) -> None:
    spec = create_sandbox(_settings(tmp_path))
    assert spec.provider == "none"
    assert spec.argv_prefix == []
    assert spec.enabled is False


def test_seatbelt_profile_denies_network_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="seatbelt")
    spec = create_sandbox(settings)

    assert spec.provider == "seatbelt"
    assert spec.argv_prefix[:2] == ["/usr/bin/sandbox-exec", "-f"]
    profile = spec.files[settings.pi_agent_dir / "sandbox.sb"]
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert f'(subpath "{settings.project_root}")' in profile
    assert f'(subpath "{settings.data_dir}")' in profile


def test_seatbelt_profile_allows_network_when_configured(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="seatbelt", sandbox_network=True)
    profile = render_seatbelt_profile(settings)
    assert "(allow network*)" in profile
    assert "(deny network*)" not in profile


def test_docker_prefix_mounts_workspace(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="docker", sandbox_image="airgent-sandbox:latest")
    spec = create_sandbox(settings)

    assert spec.provider == "docker"
    assert "--network none" in " ".join(spec.argv_prefix)
    assert f"{settings.project_root}:{settings.project_root}" in spec.argv_prefix
    assert "airgent-sandbox:latest" in spec.argv_prefix


def test_unknown_provider_raises(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        create_sandbox(_settings(tmp_path, sandbox_provider="bubblewrap"))
