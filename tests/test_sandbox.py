from __future__ import annotations

import sys
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


def test_seatbelt_read_only_profile_has_no_workspace_write(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="seatbelt", sandbox_mode="read-only")
    spec = create_sandbox(settings)

    profile = spec.files[settings.pi_agent_dir / "sandbox.sb"]
    assert f'(subpath "{settings.project_root}")' not in profile
    assert f'(subpath "{settings.data_dir}")' in profile


def test_seatbelt_full_profile_allows_all_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="seatbelt", sandbox_mode="full")
    spec = create_sandbox(settings)

    profile = spec.files[settings.pi_agent_dir / "sandbox.sb"]
    assert "(allow file-write*)" in profile


def test_seatbelt_invalid_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        create_sandbox(_settings(tmp_path, sandbox_provider="seatbelt", sandbox_mode="yolo"))


def test_docker_read_only_mount(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="docker", sandbox_mode="read-only")
    spec = create_sandbox(settings)

    assert f"{settings.project_root}:{settings.project_root}:ro" in spec.argv_prefix


def test_docker_invalid_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        create_sandbox(_settings(tmp_path, sandbox_provider="docker", sandbox_mode="yolo"))


def test_landlock_requires_linux(tmp_path: Path) -> None:
    import sys

    from app.sandbox.landlock import landlock_spec

    settings = _settings(tmp_path, sandbox_provider="landlock", sandbox_mode="workspace-write")
    if sys.platform == "linux":
        spec = landlock_spec(settings)
        assert spec.argv_prefix[0].endswith("python")
        assert "app.sandbox.landlock" in " ".join(spec.argv_prefix)
    else:
        with pytest.raises(SandboxError):
            landlock_spec(settings)


def test_landlock_read_only_omits_workspace_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sandbox_provider="landlock", sandbox_mode="read-only")
    if sys.platform != "linux":
        pytest.skip("landlock is Linux-only")

    spec = create_sandbox(settings)
    argv = " ".join(spec.argv_prefix)
    assert str(settings.project_root) not in argv
    assert str(settings.data_dir) in argv

