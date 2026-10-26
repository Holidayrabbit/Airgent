from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class SandboxError(Exception):
    """Raised when a sandbox provider cannot be configured."""


@dataclass(frozen=True)
class SandboxSpec:
    """Everything needed to launch the kernel behind a sandbox.

    argv_prefix is prepended to the pi command; files are materialized by
    kernel setup before the process starts.
    """

    provider: str
    argv_prefix: list[str] = field(default_factory=list)
    files: dict[Path, str] = field(default_factory=dict)
    env_keys: tuple[str, ...] = ()
    summary: str = "sandbox disabled"

    @property
    def enabled(self) -> bool:
        return self.provider != "none"
