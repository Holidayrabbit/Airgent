from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.skills.registry import SkillRegistry, parse_skill_md


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        data_dir=tmp_path / "data",
        skills_root=tmp_path / "project" / ".agents" / "skills",
        skills_user_root=tmp_path / "user-skills",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _write_skill(root: Path, name: str, description: str = "Test skill.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_parse_skill_md_requires_description(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: no-desc\n---\n", encoding="utf-8")
    assert parse_skill_md(skill_md, "project") is None

    skill_md2 = tmp_path / "ok" / "SKILL.md"
    skill_md2.parent.mkdir()
    skill_md2.write_text("---\nname: ok\ndescription: Fine.\n---\n", encoding="utf-8")
    entry = parse_skill_md(skill_md2, "project")
    assert entry is not None
    assert entry.name == "ok"


def test_registry_disabled_keeps_native_discovery(tmp_path: Path) -> None:
    root = tmp_path / "project" / ".agents" / "skills"
    _write_skill(root, "alpha")
    settings = _settings(tmp_path)

    registry = SkillRegistry(settings)
    assert [entry.name for entry in registry.discover()] == ["alpha"]
    assert registry.settings_overlay() is None


def test_registry_filters_disabled_skills(tmp_path: Path) -> None:
    root = tmp_path / "project" / ".agents" / "skills"
    _write_skill(root, "alpha")
    _write_skill(root, "beta")
    settings = _settings(tmp_path, skills_registry_enabled=True)

    registry = SkillRegistry(settings)
    assert registry.set_enabled("beta", False)

    overlay = registry.settings_overlay()
    assert overlay is not None
    assert len(overlay["skills"]) == 1
    assert overlay["skills"][0].endswith("alpha")
    assert registry.is_enabled("alpha") and not registry.is_enabled("beta")


def test_registry_deny_all_mode_only_runs_enabled(tmp_path: Path) -> None:
    root = tmp_path / "project" / ".agents" / "skills"
    _write_skill(root, "alpha")
    _write_skill(root, "beta")
    settings = _settings(tmp_path, skills_registry_enabled=True)

    registry = SkillRegistry(settings)
    registry.set_mode("deny_all")
    assert registry.is_enabled("alpha") is False
    registry.set_enabled("alpha", True)
    assert registry.is_enabled("alpha") is True
    assert registry.is_enabled("beta") is False

    overlay = registry.settings_overlay()
    assert overlay is not None and len(overlay["skills"]) == 1


def test_enable_unknown_skill_returns_false(tmp_path: Path) -> None:
    registry = SkillRegistry(_settings(tmp_path, skills_registry_enabled=True))
    assert registry.set_enabled("missing", True) is False
