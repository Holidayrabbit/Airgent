from __future__ import annotations

import asyncio
from pathlib import Path
import shlex

from app.agents.context import AgentRunContext
from app.core.errors import AppError

try:
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover
    def function_tool(func):  # type: ignore[misc]
        return func

    class RunContextWrapper:  # type: ignore[override]
        context: AgentRunContext


BLOCKED_COMMANDS = {
    "chattr",
    "dd",
    "diskutil",
    "doas",
    "halt",
    "kill",
    "killall",
    "launchctl",
    "mkfs",
    "mount",
    "passwd",
    "pkill",
    "poweroff",
    "reboot",
    "rm",
    "rmdir",
    "shutdown",
    "sudo",
    "su",
    "systemctl",
    "umount",
}

BLOCKED_INLINE_EXECUTORS = {"bash", "sh", "zsh"}
BLOCKED_INLINE_FLAGS = {"-c", "--command"}
DEFAULT_TIMEOUT_SECONDS = 30


def _split_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as exc:
        raise AppError(
            code="invalid_shell_command",
            message="The shell command could not be parsed.",
            status_code=400,
            details={"reason": str(exc)},
        ) from exc
    if not parts:
        raise AppError(
            code="empty_shell_command",
            message="The shell command is empty.",
            status_code=400,
        )
    return parts


def _command_name(token: str) -> str:
    return Path(token).name.lower()


def _validate_command(parts: list[str]) -> None:
    blocked_matches = sorted({_command_name(token) for token in parts if _command_name(token) in BLOCKED_COMMANDS})
    if blocked_matches:
        raise AppError(
            code="blocked_shell_command",
            message="This shell command is blocked because it includes a high-risk executable.",
            status_code=400,
            details={"blocked_commands": blocked_matches},
        )

    executable = _command_name(parts[0])
    if executable in BLOCKED_INLINE_EXECUTORS and any(flag in parts[1:] for flag in BLOCKED_INLINE_FLAGS):
        raise AppError(
            code="blocked_shell_command",
            message="Inline shell execution is blocked for the bash tool.",
            status_code=400,
            details={"blocked_executable": executable},
        )


async def _run_shell_command(
    context: AgentRunContext,
    command: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, str | int]:
    parts = _split_command(command)
    _validate_command(parts)

    process = await asyncio.create_subprocess_exec(
        *parts,
        cwd=str(context.settings.project_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise AppError(
            code="shell_command_timeout",
            message=f"The shell command timed out after {timeout_seconds} seconds.",
            status_code=408,
            details={"command": command, "timeout_seconds": timeout_seconds},
        ) from exc

    return {
        "command": command,
        "cwd": str(context.settings.project_root),
        "exit_code": process.returncode,
        "status": "completed" if process.returncode == 0 else "failed",
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


@function_tool
async def run_bash_command(
    wrapper: RunContextWrapper[AgentRunContext],
    command: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, str | int]:
    """Run a non-destructive shell command inside the current project directory."""
    return await _run_shell_command(wrapper.context, command, timeout_seconds)
