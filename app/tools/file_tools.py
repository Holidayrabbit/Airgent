from __future__ import annotations

import difflib
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


def _resolve_project_path(context: AgentRunContext, path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = context.settings.project_root / candidate
    candidate = candidate.resolve()
    project_root = context.settings.project_root.resolve()
    if candidate != project_root and project_root not in candidate.parents:
        raise AppError(
            code="path_outside_project",
            message="File access is restricted to the current project directory.",
            status_code=400,
            details={"path": str(candidate), "project_root": str(project_root)},
        )
    return candidate


def _read_text_file(context: AgentRunContext, path: str) -> dict[str, str]:
    target = _resolve_project_path(context, path)
    if not target.exists():
        raise AppError("file_not_found", f"File '{target}' was not found.", 404, {"path": str(target)})
    return {
        "path": str(target),
        "content": target.read_text(encoding="utf-8"),
    }


def _create_text_file(
    context: AgentRunContext,
    path: str,
    content: str,
    overwrite: bool = False,
) -> dict[str, str]:
    target = _resolve_project_path(context, path)
    if target.exists() and not overwrite:
        raise AppError(
            code="file_exists",
            message=f"File '{target}' already exists. Set overwrite=true to replace it.",
            status_code=409,
            details={"path": str(target)},
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target), "status": "created"}


def _edit_text_file(
    context: AgentRunContext,
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> dict[str, str]:
    target = _resolve_project_path(context, path)
    if not target.exists():
        raise AppError("file_not_found", f"File '{target}' was not found.", 404, {"path": str(target)})

    original = target.read_text(encoding="utf-8")
    if old_text not in original:
        preview = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                original.splitlines(),
                fromfile=str(target),
                tofile=str(target),
                lineterm="",
            )
        )
        raise AppError(
            code="edit_target_not_found",
            message="The target text to replace was not found in the file.",
            status_code=400,
            details={"path": str(target), "preview": preview},
        )

    updated = original.replace(old_text, new_text) if replace_all else original.replace(old_text, new_text, 1)
    target.write_text(updated, encoding="utf-8")
    return {"path": str(target), "status": "edited"}


@function_tool
async def read_file(
    wrapper: RunContextWrapper[AgentRunContext],
    path: str,
) -> dict[str, str]:
    """Read a UTF-8 text file from the current project directory."""
    return _read_text_file(wrapper.context, path)


@function_tool
async def create_file(
    wrapper: RunContextWrapper[AgentRunContext],
    path: str,
    content: str,
    overwrite: bool = False,
) -> dict[str, str]:
    """Create a UTF-8 text file inside the current project directory."""
    return _create_text_file(wrapper.context, path, content, overwrite)


@function_tool
async def edit_file(
    wrapper: RunContextWrapper[AgentRunContext],
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> dict[str, str]:
    """Edit a UTF-8 text file by replacing old_text with new_text."""
    return _edit_text_file(wrapper.context, path, old_text, new_text, replace_all)
