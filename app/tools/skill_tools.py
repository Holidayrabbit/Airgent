from __future__ import annotations

from pathlib import Path

from app.agents.context import AgentRunContext
from app.core.errors import AppError

try:
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover
    def function_tool(func):  # type: ignore[misc]
        return func

    class RunContextWrapper:  # type: ignore[override]
        context: AgentRunContext


def _skill_md_paths(skills_root: Path) -> list[Path]:
    return sorted(path for path in skills_root.glob("*/SKILL.md") if path.is_file())


@function_tool
async def list_skills(
    wrapper: RunContextWrapper[AgentRunContext],
) -> dict[str, list[dict[str, str]]]:
    """List the locally installed workflow skills available to the agent."""

    skills = []
    for skill_md in _skill_md_paths(wrapper.context.settings.skills_root):
        lines = [line.strip() for line in skill_md.read_text(encoding="utf-8").splitlines() if line.strip()]
        description = lines[1] if len(lines) > 1 else ""
        skills.append({"key": skill_md.parent.name, "description": description})
    return {"skills": skills}


@function_tool
async def load_skill(
    wrapper: RunContextWrapper[AgentRunContext],
    skill_key: str,
) -> dict[str, str]:
    """Load the full workflow instructions for a specific local skill."""

    skill_md = wrapper.context.settings.skills_root / skill_key / "SKILL.md"
    if not skill_md.exists():
        raise AppError(
            code="skill_not_found",
            message=f"Skill '{skill_key}' was not found.",
            status_code=404,
            details={"skill_key": skill_key},
        )
    return {
        "skill_key": skill_key,
        "content": skill_md.read_text(encoding="utf-8"),
    }
