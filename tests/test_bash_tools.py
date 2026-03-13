from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.tools.bash_tools import _run_shell_command, _split_command, _validate_command
from app.tools.registry import ToolRegistry


def _context(tmp_path):
    settings = SimpleNamespace(project_root=tmp_path)
    return SimpleNamespace(settings=settings)


def test_split_command_rejects_invalid_shell_syntax() -> None:
    with pytest.raises(AppError):
        _split_command('"unterminated')


def test_validate_command_blocks_rm() -> None:
    with pytest.raises(AppError) as exc_info:
        _validate_command(["rm", "-rf", "tmp"])

    assert exc_info.value.details["blocked_commands"] == ["rm"]


def test_run_shell_command_executes_inside_project_root(tmp_path) -> None:
    context = _context(tmp_path)

    result = asyncio.run(_run_shell_command(context, "pwd"))

    assert result["exit_code"] == 0
    assert result["status"] == "completed"
    assert str(tmp_path) in result["stdout"]


def test_tool_registry_hides_high_risk_tools_by_default() -> None:
    registry = ToolRegistry()

    without_high_risk = registry.resolve_enabled(["read_file", "run_bash_command"], allow_high_risk_tools=False)
    with_high_risk = registry.resolve_enabled(["read_file", "run_bash_command"], allow_high_risk_tools=True)

    assert len(without_high_risk) == 1
    assert len(with_high_risk) == 2
