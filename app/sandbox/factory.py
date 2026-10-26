from __future__ import annotations

from app.core.config import Settings
from app.sandbox.base import SandboxError, SandboxSpec


def create_sandbox(settings: Settings) -> SandboxSpec:
    """Resolve the configured sandbox provider into a launch spec."""

    provider = (settings.sandbox_provider or "none").strip().lower()
    if provider in {"", "none", "off"}:
        return SandboxSpec(provider="none")
    if provider == "seatbelt":
        from app.sandbox.seatbelt import seatbelt_spec

        return seatbelt_spec(settings)
    if provider == "docker":
        from app.sandbox.docker import docker_spec

        return docker_spec(settings)
    if provider == "landlock":
        from app.sandbox.landlock import landlock_spec

        return landlock_spec(settings)
    raise SandboxError(
        f"Unknown sandbox provider {provider!r}; "
        "expected none, seatbelt, docker, or landlock."
    )
