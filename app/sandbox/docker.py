"""Docker sandbox provider (experimental).

Runs the kernel inside a container with the workspace and Airgent data
directories bind-mounted at their host paths, so pi workspace paths stay
valid. The image must ship Node.js plus a global `pi` install and (for the
memory extension) a Python 3 interpreter; see docs/sandbox.md for a recipe.
"""

from __future__ import annotations

import os

from app.core.config import Settings
from app.sandbox.base import SandboxError, SandboxSpec

# Environment variables forwarded into the container. Deliberately a whitelist.
_DOCKER_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_MODE",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AIRGENT_MODEL",
    "PI_CODING_AGENT_DIR",
    "PI_CODING_AGENT_SESSION_DIR",
    "HOME",
    "PATH",
)


def docker_spec(settings: Settings) -> SandboxSpec:
    if not settings.project_root.is_dir():
        raise SandboxError(f"workspace directory missing: {settings.project_root}")

    argv = [
        "docker",
        "run",
        "--rm",
        "-i",
        "-w",
        str(settings.project_root),
        "-v",
        f"{settings.project_root}:{settings.project_root}",
        "-v",
        f"{settings.data_dir}:{settings.data_dir}",
    ]
    if not settings.sandbox_network:
        argv.extend(["--network", "none"])
    for extra in filter(None, (part.strip() for part in settings.sandbox_docker_args.split(","))):
        argv.append(extra)
    for key in _DOCKER_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            argv.extend(["-e", f"{key}={value}"])
    argv.append(settings.sandbox_image)

    network = "bridge" if settings.sandbox_network else "none"
    return SandboxSpec(
        provider="docker",
        argv_prefix=argv,
        summary=f"docker image={settings.sandbox_image} network={network}",
    )
