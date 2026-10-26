"""Skill registry: opt-in enable/disable control over pi's skill discovery.

By default Airgent stays a plain pi wrapper — skills come from pi's native
`.agents/skills` discovery and this module is inert. With
`AIRGENT_SKILLS_REGISTRY=1`, Airgent writes a `settings.json` overlay into
the isolated pi agent dir whose `skills` array whitelists exactly the
enabled skill directories (project `.agents/skills`, user `~/.agents/skills`,
plus any extra dirs), giving per-skill kill switches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.core.config import Settings


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    path: Path
    source: str  # "project" | "user" | "extra"


@dataclass
class RegistryState:
    mode: str = "allow_all"  # allow_all | deny_all
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    extra_dirs: list[str] = field(default_factory=list)


def parse_skill_md(path: Path, source: str) -> SkillEntry | None:
    """Parse a SKILL.md into an entry; None when there is no usable frontmatter."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    try:
        frontmatter = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    name = str(frontmatter.get("name") or "").strip()
    if not name:
        name = path.parent.name
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        # pi does not load skills without a description; mirror that.
        return None
    return SkillEntry(name=name, description=description[:1024], path=path.parent, source=source)


def _scan_skills_dir(root: Path, source: str) -> list[SkillEntry]:
    if not root.is_dir():
        return []
    entries: list[SkillEntry] = []
    for skill_md in sorted(root.glob("**/SKILL.md")):
        entry = parse_skill_md(skill_md, source)
        if entry is not None:
            entries.append(entry)
    return entries


class SkillRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_file = settings.skills_registry_file

    # ------------------------------------------------------------------ state
    def load_state(self) -> RegistryState:
        if not self.state_file.is_file():
            return RegistryState()
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return RegistryState()
        if not isinstance(payload, dict):
            return RegistryState()
        mode = payload.get("mode")
        return RegistryState(
            mode=mode if mode in {"allow_all", "deny_all"} else "allow_all",
            enabled=[str(item) for item in payload.get("enabled") or []],
            disabled=[str(item) for item in payload.get("disabled") or []],
            extra_dirs=[str(item) for item in payload.get("extra_dirs") or []],
        )

    def save_state(self, state: RegistryState) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(
                {
                    "mode": state.mode,
                    "enabled": state.enabled,
                    "disabled": state.disabled,
                    "extra_dirs": state.extra_dirs,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -------------------------------------------------------------- discovery
    def discover(self) -> list[SkillEntry]:
        settings = self.settings
        entries = list(_scan_skills_dir(settings.skills_root, "project"))
        seen = {entry.name for entry in entries}
        for entry in _scan_skills_dir(settings.skills_user_dir, "user"):
            if entry.name not in seen:
                entries.append(entry)
                seen.add(entry.name)
        for raw in self.load_state().extra_dirs:
            extra = Path(raw).expanduser().resolve()
            for entry in _scan_skills_dir(extra, "extra"):
                if entry.name not in seen:
                    entries.append(entry)
                    seen.add(entry.name)
        return entries

    # ---------------------------------------------------------------- control
    def is_enabled(self, name: str) -> bool:
        state = self.load_state()
        if state.mode == "deny_all":
            return name in state.enabled and name not in state.disabled
        return name not in state.disabled

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Flip one skill; returns False when the skill is unknown."""

        known = {entry.name for entry in self.discover()}
        if name not in known:
            return False
        state = self.load_state()
        if enabled:
            state.disabled = [item for item in state.disabled if item != name]
            if name not in state.enabled:
                state.enabled.append(name)
        else:
            state.enabled = [item for item in state.enabled if item != name]
            if name not in state.disabled:
                state.disabled.append(name)
        self.save_state(state)
        return True

    def set_mode(self, mode: str) -> None:
        if mode not in {"allow_all", "deny_all"}:
            raise ValueError(f"unknown registry mode: {mode}")
        state = self.load_state()
        state.mode = mode
        self.save_state(state)

    def add_extra_dir(self, raw: str) -> None:
        state = self.load_state()
        resolved = str(Path(raw).expanduser().resolve())
        if resolved not in state.extra_dirs:
            state.extra_dirs.append(resolved)
        self.save_state(state)

    # ----------------------------------------------------------------- output
    def enabled_entries(self) -> list[SkillEntry]:
        return [entry for entry in self.discover() if self.is_enabled(entry.name)]

    def settings_overlay(self) -> dict | None:
        """The pi settings.json `skills` array, or None to keep native discovery.

        Overlay is produced only when the registry is enabled AND some skill is
        actually filtered out; otherwise pi's native scan is left untouched.
        """

        if not self.settings.skills_registry_enabled:
            return None
        state = self.load_state()
        if state.mode == "allow_all" and not state.disabled:
            return None
        enabled = self.enabled_entries()
        return {"skills": [str(entry.path) for entry in enabled]}
